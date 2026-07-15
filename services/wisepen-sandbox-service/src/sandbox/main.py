from __future__ import annotations

import asyncio
import importlib

import uvicorn

from common.logger import error, info, setup_logging_intercept
from common.observability import instrument_fastapi_app, setup_observability
from common.web.exception_handlers import setup_global_exception_handlers
from common.web.middleware import SecurityHeaderMiddleware
from sandbox.api import create_app
from sandbox.core.config.app_settings import settings
from sandbox.core.config.bootstrap_settings import bootstrap_settings
from sandbox.core.config.nacos import nacos_client_manager
from sandbox.leader import InMemoryLeaderLease
from sandbox.queue_jurfal.models import SandboxSpec
from sandbox.queue_jurfal.pool import SandboxPool
from sandbox.queue_jurfal.repository import InMemorySandboxRepository
from sandbox.queue_jurfal.scheduler import SandboxScheduler
from sandbox.queue_jurfal.watcher import Watcher
from sandbox.queue_jurfal.workspace import LocalWorkspaceStore


setup_logging_intercept(bootstrap_settings.LOG_LEVEL)
setup_observability(
    service_name=bootstrap_settings.SERVICE_NAME,
    environment=bootstrap_settings.PROFILE,
)


def _load_provider(target: str) -> object:
    if not target:
        raise RuntimeError("SANDBOX_PROVIDER_FACTORY 必须指向 SandboxProvider 工厂")
    module_name, factory_name = target.split(":", 1)
    factory = getattr(importlib.import_module(module_name), factory_name)
    return factory.from_environment()


repository = InMemorySandboxRepository()
min_ready = settings.SANDBOX_MIN_READY
target_ready = settings.SANDBOX_TARGET_READY
pool = SandboxPool(
    repository,
    settings.SANDBOX_LEASE_TTL_SECONDS,
    min_ready=min_ready,
    target_ready=target_ready,
)
provider = _load_provider(settings.SANDBOX_PROVIDER_FACTORY)
scheduler = SandboxScheduler(
    pool,
    repository,
    provider,
    LocalWorkspaceStore(settings.SANDBOX_WORKSPACE_ROOT),
    destroy_timeout_seconds=settings.SANDBOX_DESTROY_TIMEOUT_SECONDS,
    destroy_max_retries=3,
)
leader_lease = InMemoryLeaderLease()
watcher = Watcher(
    pool,
    repository,
    provider,
    SandboxSpec(image=settings.SANDBOX_IMAGE),
    scheduler=scheduler,
    leader_lease=leader_lease,
    target_ready=target_ready,
    min_ready=min_ready,
    reserve=settings.SANDBOX_READY_RESERVE,
    max_create_batch=settings.SANDBOX_MAX_CREATE_BATCH,
    warmup_timeout_seconds=settings.SANDBOX_WARMUP_TIMEOUT_SECONDS,
    destroy_timeout_seconds=settings.SANDBOX_DESTROY_TIMEOUT_SECONDS,
    max_retries=settings.SANDBOX_WARMUP_MAX_RETRIES,
)
app = create_app(scheduler, pool)
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
    app.state.watcher_task = asyncio.create_task(watcher.run())
    info(
        "服务已就绪。",
        service=bootstrap_settings.SERVICE_NAME,
        port=bootstrap_settings.SERVICE_PORT,
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    info("服务正在停止。", service=bootstrap_settings.SERVICE_NAME)
    watcher.stop()
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
