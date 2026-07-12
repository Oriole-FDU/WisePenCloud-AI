from __future__ import annotations

import importlib
import os

import uvicorn

from sandbox.api import create_app
from sandbox.models import SandboxSpec
from sandbox.pool import SandboxPool
from sandbox.repository import InMemorySandboxRepository
from sandbox.scheduler import SandboxScheduler
from sandbox.watcher import Watcher
from sandbox.workspace import LocalWorkspaceStore


def _load_provider() -> object:
    target = os.getenv("SANDBOX_PROVIDER_FACTORY")
    if not target:
        raise RuntimeError(
            "SANDBOX_PROVIDER_FACTORY must point to a SandboxProvider factory"
        )
    module_name, factory_name = target.split(":", 1)
    factory = getattr(importlib.import_module(module_name), factory_name)
    return factory.from_environment()


repository = InMemorySandboxRepository()
pool = SandboxPool(repository, int(os.getenv("SANDBOX_LEASE_TTL_SECONDS", "1800")))
provider = _load_provider()
scheduler = SandboxScheduler(
    pool,
    repository,
    provider,
    LocalWorkspaceStore(os.getenv("SANDBOX_WORKSPACE_ROOT", "/tmp/wisepen-workspaces")),
)
watcher = Watcher(
    pool,
    repository,
    provider,
    SandboxSpec(image=os.getenv("SANDBOX_IMAGE", "ghcr.io/agent-infra/sandbox:latest")),
    target_ready=int(os.getenv("SANDBOX_TARGET_READY", "2")),
    min_ready=int(os.getenv("SANDBOX_MIN_READY", "1")),
    reserve=int(os.getenv("SANDBOX_READY_RESERVE", "0")),
    max_create_batch=int(os.getenv("SANDBOX_MAX_CREATE_BATCH", "2")),
    warmup_timeout_seconds=float(os.getenv("SANDBOX_WARMUP_TIMEOUT_SECONDS", "60")),
    destroy_timeout_seconds=float(os.getenv("SANDBOX_DESTROY_TIMEOUT_SECONDS", "60")),
    max_retries=int(os.getenv("SANDBOX_WARMUP_MAX_RETRIES", "3")),
)
app = create_app(scheduler, pool)


@app.on_event("startup")
async def startup() -> None:
    import asyncio

    app.state.watcher_task = asyncio.create_task(watcher.run())


@app.on_event("shutdown")
async def shutdown() -> None:
    watcher.stop()
    task = getattr(app.state, "watcher_task", None)
    if task:
        task.cancel()


if __name__ == "__main__":
    uvicorn.run(
        "sandbox.main:app",
        host=os.getenv("SANDBOX_HOST", "0.0.0.0"),
        port=int(os.getenv("SANDBOX_PORT", "9001")),
    )
