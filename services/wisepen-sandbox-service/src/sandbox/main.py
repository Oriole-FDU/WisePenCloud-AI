from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn

from common.logger import error, info, setup_logging_intercept
from common.observability import instrument_fastapi_app, setup_observability
from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware
from sandbox.api import create_app
from sandbox.api.endpoints import health, pool, sandbox
from sandbox.container import container
from sandbox.application.services.sandbox_session import SandboxSessionService
from sandbox.gateway.binding import VncBinding
from sandbox.core.config.app_settings import settings
from sandbox.core.config.bootstrap_settings import bootstrap_settings
from sandbox.core.config.nacos import nacos_client_manager
from sandbox.transport.mcp import build_sandbox_mcp


setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME,
    environment=bootstrap_settings.PROFILE,
)

# 容器在模块加载时构建，FastAPI 路由、MCP 和 VNC 网关共享同一个 Scheduler。
container.config.from_dict(settings.model_dump())
container.wire(modules=[health, pool, sandbox])
sandbox_session = SandboxSessionService(
    container.scheduler(),
    execution_default_timeout_ms=settings.SANDBOX_EXECUTION_DEFAULT_TIMEOUT_MS,
    execution_max_timeout_ms=settings.SANDBOX_EXECUTION_MAX_TIMEOUT_MS,
)

mcp_server = build_sandbox_mcp(sandbox_session)
vnc_binding = VncBinding(
    sandbox_session,
    idle_timeout_seconds=settings.SANDBOX_VNC_IDLE_TIMEOUT_SECONDS,
)


@asynccontextmanager
async def lifespan(app):
    async with mcp_server.session_manager.run():
        info("服务正在启动。", service=bootstrap_settings.SERVICE_NAME)

        use_nacos = str(os.getenv("CHAT_USE_NACOS") or "").strip().lower() in ("1", "true", "yes")
        # Docker worker 前置条件：DEV 模式下允许跳过（本地可能无 AIO 镜像）。
        if not bootstrap_settings.IS_DEV or use_nacos:
            await container.provider().validate_deployment()
        else:
            info("跳过部署环境验证（DEV 模式）。")

        # 启动对账必须早于 watcher 补池：Repository 是权威，Docker label 只用于发现孤儿。
        await container.startup_reconciler().reconcile()

        if use_nacos:
            await nacos_client_manager.register_instance()
        else:
            info("跳过 Nacos 注册（CHAT_USE_NACOS 未设置）。")

        cleanup_stop = asyncio.Event()

        async def cleanup_loop() -> None:
            # 远程桌面是浏览器跳转式连接，前端不一定显式释放，因此后台按空闲时间回收。
            while not cleanup_stop.is_set():
                try:
                    await asyncio.wait_for(
                        cleanup_stop.wait(),
                        timeout=settings.SANDBOX_VNC_IDLE_CLEANUP_INTERVAL_SECONDS,
                    )
                except asyncio.TimeoutError:
                    await vnc_binding.cleanup_idle()

        cleanup_task = asyncio.create_task(cleanup_loop())
        app.state.watcher_task = asyncio.create_task(container.watcher().run())
        info(
            "服务已就绪。",
            service=bootstrap_settings.SERVICE_NAME,
            port=bootstrap_settings.SERVICE_PORT,
        )
        try:
            yield
        finally:
            info("服务正在停止。", service=bootstrap_settings.SERVICE_NAME)
            container.watcher().stop()
            task = getattr(app.state, "watcher_task", None)
            if task:
                task.cancel()
            cleanup_stop.set()
            cleanup_task.cancel()
            # 并行执行：优雅关闭容器 + Docker label 兜底清理。
            # cleanup_owned 通过 docker rm -f 按 label 批量删除，不依赖
            # Repository 状态，能清理 shutdown() 超时后残留的容器。
            scheduler_shutdown = asyncio.create_task(
                container.scheduler().shutdown()
            )
            provider = container.provider()
            cleanup_owned = getattr(provider, "cleanup_owned", None)
            cleanup_task_shutdown = (
                asyncio.create_task(cleanup_owned())
                if cleanup_owned is not None
                else None
            )
            # 等待两路清理完成；任一失败只记日志不阻塞对方。
            scheduler_errors = await scheduler_shutdown
            for exc in scheduler_errors:
                error("sandbox graceful shutdown failed.", exc=exc)
            if cleanup_task_shutdown is not None:
                try:
                    count = await cleanup_task_shutdown
                    info(f"label 兜底清理了 {count} 个残留容器。")
                except Exception as exc:
                    error("sandbox label 清理失败。", exc=exc)
            if use_nacos:
                try:
                    await nacos_client_manager.deregister_instance()
                except Exception as exc:
                    error("nacos 实例注销失败。", service=bootstrap_settings.SERVICE_NAME, exc=exc)


app = create_app(
    mcp_app=mcp_server.streamable_http_app(),
    lifespan=lifespan,
    vnc_binding=vnc_binding,
)
instrument_fastapi_app(app)
app.add_middleware(SecurityHeaderMiddleware, from_source_secret=settings.FROM_SOURCE_SECRET)
setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)


if __name__ == "__main__":
    uvicorn.run(
        "sandbox.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
    )
