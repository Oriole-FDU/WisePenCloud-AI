from __future__ import annotations

import httpx
import pytest

from sandbox.api import create_app
from sandbox.models import SandboxSpec
from sandbox.pool import SandboxPool
from sandbox.repository import InMemorySandboxRepository
from sandbox.scheduler import SandboxScheduler
from sandbox.watcher import Watcher

from test_lifecycle import FakeProvider, FakeWorkspace


@pytest.mark.asyncio
async def test_internal_api_requires_fencing_and_exposes_metrics():
    provider = FakeProvider()
    repository = InMemorySandboxRepository()
    pool = SandboxPool(repository)
    watcher = Watcher(pool, repository, provider, SandboxSpec("test"), target_ready=1)
    await watcher.reconcile()
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())
    app = create_app(scheduler, pool)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/healthz")
        assert health.status_code == 200
        allocated = await client.post(
            "/internal/sandboxes/allocate",
            json={"request_id": "req-api", "tenant_id": "tenant", "workspace_id": "workspace"},
        )
        assert allocated.status_code == 200
        lease = allocated.json()
        invalid = await client.post(
            f"/internal/leases/{lease['lease_id']}/execute",
            json={
                "request_id": "exec-api",
                "tenant_id": "tenant",
                "workspace_id": "workspace",
                "fencing_token": lease["fencing_token"] + 1,
                "operation": "shell_exec",
            },
        )
        assert invalid.status_code == 409
        assert invalid.json()["detail"] == "FENCING_REJECTED"
        metrics = await client.get("/internal/pool/metrics")
        assert metrics.status_code == 200
        assert metrics.json()["generation"] > 0
