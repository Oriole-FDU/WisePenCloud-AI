from __future__ import annotations

import httpx
import pytest
from dependency_injector import providers

from sandbox.api import create_app
from sandbox.api.endpoints import health, pool as pool_endpoints, sandbox as sandbox_endpoints
from sandbox.container import container
from sandbox.domain.entities import SandboxSpec
from sandbox.application.services import SandboxPool, SandboxScheduler, Watcher
from sandbox.core.storage.memory import MemorySandboxRepository

from test_lifecycle import FakeProvider, FakeWorkspace


@pytest.fixture(autouse=True)
def wire_endpoint_container():
    """Wire endpoints once per test and clear global-container overrides afterwards."""
    container.wire(modules=[health, pool_endpoints, sandbox_endpoints])
    yield
    container.pool.reset_override()
    container.scheduler.reset_override()


def create_test_app(pool: SandboxPool, scheduler: SandboxScheduler):
    container.pool.override(providers.Object(pool))
    container.scheduler.override(providers.Object(scheduler))
    return create_app()


@pytest.mark.asyncio
async def test_internal_api_requires_fencing_and_exposes_metrics():
    provider = FakeProvider()
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository)
    watcher = Watcher(pool, repository, provider, SandboxSpec("test"), target_ready=1)
    await watcher.reconcile()
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())
    app = create_test_app(pool, scheduler)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        ready = await client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        allocated = await client.post(
            "/internal/sandboxes/allocate",
            json={"request_id": "req-api", "tenant_id": "tenant", "workspace_id": "workspace"},
        )
        assert allocated.status_code == 200
        allocated_body = allocated.json()
        assert allocated_body["code"] == 200
        lease = allocated_body["data"]
        assert set(lease) == {
            "lease_id",
            "request_id",
            "sandbox_id",
            "tenant_id",
            "workspace_id",
            "expires_at",
            "fencing_token",
            "user_binding_id",
            "user_idle_expires_at",
            "container_reused",
            "workspace_reused",
            "endpoint",
        }
        assert lease["endpoint"]["base_url"]

        retried = await client.post(
            "/internal/sandboxes/allocate",
            json={"request_id": "req-api", "tenant_id": "tenant", "workspace_id": "workspace"},
        )
        assert retried.status_code == 200
        assert retried.json()["data"]["lease_id"] == lease["lease_id"]

        conflict = await client.post(
            "/internal/sandboxes/allocate",
            json={"request_id": "req-api", "tenant_id": "other-tenant", "workspace_id": "workspace"},
        )
        assert conflict.status_code == 200
        assert conflict.json()["code"] == 46005

        status = await client.get(f"/internal/sandboxes/{lease['sandbox_id']}")
        assert status.status_code == 200
        status_body = status.json()
        assert status_body["code"] == 200
        status_data = status_body["data"]
        assert status_data["ref"]["sandbox_id"] == lease["sandbox_id"]
        assert "provider_id" not in status_data
        assert "metadata" not in status_data["ref"]
        assert "readiness_token" not in status_data
        assert "token" not in (status_data["ref"]["endpoint"] or {})

        executed = await client.post(
            f"/internal/leases/{lease['lease_id']}/execute",
            json={
                "request_id": "exec-success",
                "tenant_id": "tenant",
                "workspace_id": "workspace",
                "fencing_token": lease["fencing_token"],
                "operation": "shell_exec",
            },
        )
        assert executed.status_code == 200
        assert executed.json()["data"] == {
            "request_id": "exec-success",
            "status": "succeeded",
            "data": {"ok": True},
        }

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

        released = await client.post(
            f"/internal/leases/{lease['lease_id']}/release",
            json={"fencing_token": lease["fencing_token"]},
        )
        assert released.status_code == 200
        assert released.json()["data"] == {"status": "released"}

        status = await client.get(f"/internal/sandboxes/{lease['sandbox_id']}")
        assert status.json()["data"]["state"] == "user_idle"

        deleted = await client.post(
            "/internal/sandbox-workspaces/delete",
            json={"tenant_id": "tenant", "workspace_id": "workspace"},
        )
        assert deleted.json()["data"]["status"] == "deleted"


@pytest.mark.asyncio
async def test_health_readiness_and_request_validation_contracts():
    provider = FakeProvider()
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())
    app = create_test_app(pool, scheduler)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ready = await client.get("/readyz")
        assert ready.status_code == 503
        assert ready.json() == {
            "detail": {
                "code": "MIN_READY_NOT_REACHED",
                "ready": 0,
                "min_ready": 1,
            }
        }

        invalid = await client.post(
            "/internal/sandboxes/allocate",
            json={"request_id": "req-invalid", "tenant_id": "bad tenant", "workspace_id": "workspace"},
        )
        assert invalid.status_code == 422


def test_openapi_documents_internal_sandbox_contracts():
    provider = FakeProvider()
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())
    app = create_test_app(pool, scheduler)
    document = app.openapi()

    assert document["info"]["version"] == "0.2.0"
    assert document["tags"]
    for path, method in (
        ("/healthz", "get"),
        ("/readyz", "get"),
        ("/internal/sandboxes/allocate", "post"),
        ("/internal/leases/{lease_id}/execute", "post"),
        ("/internal/leases/{lease_id}/release", "post"),
        ("/internal/sandbox-workspaces/delete", "post"),
        ("/internal/user-sandboxes/destroy", "post"),
        ("/internal/sandboxes/{sandbox_id}", "get"),
        ("/internal/pool/metrics", "get"),
    ):
        operation = document["paths"][path][method]
        assert operation["summary"]
        assert operation["description"]
        assert "responses" in operation
