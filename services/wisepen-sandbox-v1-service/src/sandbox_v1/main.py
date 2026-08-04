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
    return str(os.getenv("CHAT_USE_NACOS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


@asynccontextmanager
async def lifespan(app):
    """Start pool maintenance only when a runtime provider is injected."""
    runtime_provider = None
    watcher_task = None

    try:
        runtime_provider = container.provider()
    except Exception:
        # The core service can expose health and metrics before integration
        # supplies a concrete container runtime provider.
        info("sandbox runtime provider is not configured; watcher is dormant")

    if runtime_provider is not None:
        await runtime_provider.validate_deployment()
        await container.startup_reconciler().reconcile()
        watcher_task = asyncio.create_task(container.watcher().run())

    use_nacos = _use_nacos()
    if use_nacos:
        await nacos_client_manager.register_instance()

    app.state.watcher_task = watcher_task
    info("sandbox pool core started", service=bootstrap_settings.SERVICE_NAME)
    try:
        yield
    finally:
        if watcher_task:
            container.watcher().stop()
            watcher_task.cancel()
            with suppress(asyncio.CancelledError):
                await watcher_task

        if runtime_provider is not None:
            try:
                await runtime_provider.cleanup_owned()
            except Exception as exc:
                error("sandbox runtime cleanup failed", exc=exc)

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
