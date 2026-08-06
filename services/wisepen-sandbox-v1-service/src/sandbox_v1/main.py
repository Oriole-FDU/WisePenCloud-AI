from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress

import uvicorn
from common.logger import error, info, setup_logging_intercept
from common.observability import instrument_fastapi_app, setup_observability
from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware

from sandbox_v1.api import create_app
from sandbox_v1.api.endpoints import health, pool
from sandbox_v1.container import container
from sandbox_v1.core.config.app_settings import settings
from sandbox_v1.core.config.bootstrap_settings import bootstrap_settings
from sandbox_v1.core.config.nacos import nacos_client_manager


setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME,
    environment=bootstrap_settings.PROFILE,
)

container.config.from_dict(settings.model_dump())
container.wire(modules=[health, pool])


def _use_nacos() -> bool:
    """读取环境变量，决定本进程是否启用 Nacos 注册。"""

    return str(os.getenv("CHAT_USE_NACOS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


@asynccontextmanager
async def lifespan(app):
    """管理 FastAPI 生命周期内的沙箱核心启动与停机。

    启动时先探测是否注入 runtime provider；若存在则校验部署、执行启动对账，
    并启动 watcher 后台维护任务。随后按环境开关注册 Nacos。停机时反向清理：
    停止 watcher、清理本进程拥有的 runtime 资源，并注销 Nacos 实例。
    """

    runtime_provider = None
    watcher_task = None

    # provider 是部署层注入的可选依赖；未注入时服务只暴露健康检查和指标。
    try:
        runtime_provider = container.provider()
    except Exception:
        # The core service can expose health and metrics before integration
        # supplies a concrete container runtime provider.
        info("sandbox runtime provider is not configured; watcher is dormant")

    if runtime_provider is not None:
        # runtime provider 可用时，先校验部署，再对账历史容器，最后启动后台补池。
        await runtime_provider.validate_deployment()
        await container.startup_reconciler().reconcile()
        watcher_task = asyncio.create_task(container.watcher().run())

    # Nacos 注册由环境变量控制，本地开发默认不注册。
    use_nacos = _use_nacos()
    if use_nacos:
        await nacos_client_manager.register_instance()

    app.state.watcher_task = watcher_task
    info("sandbox pool core started", service=bootstrap_settings.SERVICE_NAME)
    try:
        yield
    finally:
        # 先停止 watcher，避免停机期间继续创建或销毁容器。
        if watcher_task:
            container.watcher().stop()
            watcher_task.cancel()
            with suppress(asyncio.CancelledError):
                await watcher_task

        # provider 存在时清理本进程拥有的 runtime 资源；失败只记录，不阻塞后续注销。
        if runtime_provider is not None:
            try:
                await runtime_provider.cleanup_owned()
            except Exception as exc:
                error("sandbox runtime cleanup failed", exc=exc)

        # 如果启动时注册过 Nacos，停机时尽量注销实例。
        if use_nacos:
            try:
                await nacos_client_manager.deregister_instance()
            except Exception as exc:
                error("Nacos instance deregistration failed", exc=exc)


app = create_app(lifespan=lifespan)
instrument_fastapi_app(app)
app.add_middleware(
    SecurityHeaderMiddleware,
    from_source_secret=settings.FROM_SOURCE_SECRET,
)
setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)


if __name__ == "__main__":
    uvicorn.run(
        "sandbox_v1.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
    )
