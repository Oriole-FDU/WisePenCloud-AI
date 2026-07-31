from __future__ import annotations

import asyncio
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


setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME,
    environment=bootstrap_settings.PROFILE,
)

# 容器在模块加载时构建，FastAPI 路由、MCP 和 VNC 网关共享同一个 Scheduler。
container.config.from_dict(settings.model_dump())
container.wire(modules=[health, pool, sandbox])
sandbox_session = SandboxSessionService(container.scheduler())
from sandbox.transport.mcp import build_sandbox_mcp

mcp_server = build_sandbox_mcp(sandbox_session)
vnc_binding = VncBinding(sandbox_session)


@asynccontextmanager
async def lifespan(app):
    async with mcp_server.session_manager.run():
        info("服务正在启动。", service=bootstrap_settings.SERVICE_NAME)
        # Docker worker 前置条件和 Nacos 注册均为启动硬依赖。
        await container.provider().validate_deployment()
        await nacos_client_manager.register_instance()

        cleanup_stop = asyncio.Event()

        async def cleanup_loop() -> None:
            # 远程桌面是浏览器跳转式连接，前端不一定显式释放，因此后台按空闲时间回收。
            while not cleanup_stop.is_set():
                try:
                    await asyncio.wait_for(cleanup_stop.wait(), timeout=300)
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
            for exc in await container.scheduler().shutdown():
                error("sandbox graceful shutdown failed.", exc=exc)
            provider = container.provider()
            cleanup_owned = getattr(provider, "cleanup_owned", None)
            if cleanup_owned is not None:
                try:
                    await cleanup_owned()
                except Exception as exc:
                    error("sandbox worker 清理失败。", exc=exc)
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
