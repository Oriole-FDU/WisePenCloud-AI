from __future__ import annotations

import asyncio

import uvicorn

from common.logger import error, info, setup_logging_intercept
from common.observability import instrument_fastapi_app, setup_observability
from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware
from sandbox.api import create_app
from sandbox.container import build_container
from sandbox.core.config.app_settings import settings
from sandbox.core.config.bootstrap_settings import bootstrap_settings
from sandbox.core.config.nacos import nacos_client_manager


setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME,
    environment=bootstrap_settings.PROFILE,
)

container = build_container()
app = create_app(container.scheduler, container.pool)
instrument_fastapi_app(app)
app.add_middleware(SecurityHeaderMiddleware, from_source_secret=settings.FROM_SOURCE_SECRET)
setup_global_exception_handlers(app, is_dev=bootstrap_settings.IS_DEV)


@app.on_event("startup")
async def startup() -> None:
    info("服务正在启动。", service=bootstrap_settings.SERVICE_NAME)
    try:
        await nacos_client_manager.register_instance()
    except Exception as exc:
        error("nacos 实例注册失败。", service=bootstrap_settings.SERVICE_NAME, exc=exc)
    app.state.watcher_task = asyncio.create_task(container.watcher.run())
    info(
        "服务已就绪。",
        service=bootstrap_settings.SERVICE_NAME,
        port=bootstrap_settings.SERVICE_PORT,
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    info("服务正在停止。", service=bootstrap_settings.SERVICE_NAME)
    container.watcher.stop()
    task = getattr(app.state, "watcher_task", None)
    if task:
        task.cancel()
    try:
        await nacos_client_manager.deregister_instance()
    except Exception as exc:
        error("nacos 实例注销失败。", service=bootstrap_settings.SERVICE_NAME, exc=exc)


if __name__ == "__main__":
    uvicorn.run(
        "sandbox.main:app",
        host=bootstrap_settings.SERVICE_HOST,
        port=bootstrap_settings.SERVICE_PORT,
    )
