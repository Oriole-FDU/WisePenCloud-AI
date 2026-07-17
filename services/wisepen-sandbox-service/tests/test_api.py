from __future__ import annotations

import httpx
import pytest

from sandbox.api import create_app
from sandbox.domain.entities import SandboxSpec
from sandbox.application.services import SandboxPool, SandboxScheduler, Watcher
from sandbox.core.storage.memory import MemorySandboxRepository

from test_lifecycle import FakeProvider, FakeWorkspace


@pytest.mark.asyncio
async def test_internal_api_requires_fencing_and_exposes_metrics():
    provider = FakeProvider()
    repository = MemorySandboxRepository()
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
        allocated_body = allocated.json()
        assert allocated_body["code"] == 200
        lease = allocated_body["data"]
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
        assert invalid.status_code == 200
        invalid_body = invalid.json()
        assert invalid_body["code"] == 46004
        assert invalid_body["data"] is None
        metrics = await client.get("/internal/pool/metrics")
        assert metrics.status_code == 200
        metrics_body = metrics.json()
        assert metrics_body["code"] == 200
        assert metrics_body["data"]["generation"] > 0
