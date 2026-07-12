from __future__ import annotations

import asyncio

import pytest

from sandbox.models import (
    Endpoint,
    ExecutionRequest,
    ExecutionResult,
    SandboxRecord,
    SandboxRef,
    SandboxSpec,
    SandboxState,
    WorkspaceSnapshot,
    Health,
)
from sandbox.errors import FencingRejectedError, SandboxUnavailableError, WorkspaceSyncError
from sandbox.pool import SandboxPool
from sandbox.repository import InMemorySandboxRepository
from sandbox.scheduler import SandboxScheduler
from sandbox.watcher import Watcher


class FakeWorkspace:
    def __init__(self) -> None:
        self.commits: list[tuple[WorkspaceSnapshot, str]] = []

    async def snapshot(self, tenant_id: str, workspace_id: str) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(tenant_id, workspace_id, {"main.py": "print(1)"})

    async def commit(self, snapshot: WorkspaceSnapshot, lease_id: str, fencing_token: int = 0) -> None:
        self.commits.append((snapshot, lease_id))


class FakeProvider:
    def __init__(self) -> None:
        self.created = 0
        self.destroyed: list[str] = []
        self.prepared = 0
        self.fail_prepare = False

    async def create(self, spec: SandboxSpec) -> SandboxRef:
        self.created += 1
        return SandboxRef(
            sandbox_id=f"sb-{self.created}",
            provider_id=f"provider-{self.created}",
            endpoint=Endpoint(f"http://127.0.0.1:{8000 + self.created}"),
        )

    async def wait_ready(self, sandbox: SandboxRef, timeout_seconds: float) -> Health:
        return Health(True, "ready")

    async def health(self, sandbox: SandboxRef) -> Health:
        return Health(True, "ready")

    async def prepare_workspace(self, sandbox: SandboxRef, workspace: WorkspaceSnapshot) -> None:
        self.prepared += 1
        if self.fail_prepare:
            raise RuntimeError("prepare failed")

    async def activate(self, sandbox: SandboxRef, lease) -> Endpoint:
        return sandbox.endpoint

    async def forward(self, sandbox: SandboxRef, request: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(request.request_id, "succeeded", {"ok": True})

    async def export_workspace(self, sandbox: SandboxRef, tenant_id: str, workspace_id: str) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(tenant_id, workspace_id, {"result.txt": "done"})

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        self.destroyed.append(sandbox.sandbox_id)


async def ready_pool(provider: FakeProvider):
    repository = InMemorySandboxRepository()
    pool = SandboxPool(repository)
    record = SandboxRecord(
        ref=await provider.create(SandboxSpec("test")),
        state=SandboxState.WARMING,
    )
    await pool.add_ready(record)
    return repository, pool


@pytest.mark.asyncio
async def test_checkout_is_atomic_under_concurrency():
    provider = FakeProvider()
    _, pool = await ready_pool(provider)

    async def checkout(request_id):
        try:
            return await pool.checkout(request_id, "tenant", "workspace")
        except Exception as exc:
            return exc

    results = await asyncio.gather(checkout("req-1"), checkout("req-2"))
    assert sum(not isinstance(result, Exception) for result in results) == 1


@pytest.mark.asyncio
async def test_scheduler_releases_by_committing_then_destroying():
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    workspace = FakeWorkspace()
    scheduler = SandboxScheduler(pool, repository, provider, workspace)

    lease = await scheduler.allocate("req-1", "tenant", "workspace")
    result = await scheduler.execute(
        lease.lease_id,
        ExecutionRequest("exec-1", "tenant", "workspace", "shell_exec", fencing_token=lease.fencing_token),
    )
    await scheduler.release(lease.lease_id, lease.fencing_token)

    assert result.status == "succeeded"
    assert workspace.commits[0][1] == lease.lease_id
    assert provider.destroyed == [lease.sandbox_id]


@pytest.mark.asyncio
async def test_allocation_failure_destroys_sandbox():
    provider = FakeProvider()
    provider.fail_prepare = True
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())

    with pytest.raises(SandboxUnavailableError):
        await scheduler.allocate("req-2", "tenant", "workspace")
    assert provider.destroyed


@pytest.mark.asyncio
async def test_watcher_fills_only_the_ready_deficit():
    provider = FakeProvider()
    repository = InMemorySandboxRepository()
    pool = SandboxPool(repository)
    watcher = Watcher(
        pool,
        repository,
        provider,
        SandboxSpec("test"),
        target_ready=2,
        warmup_timeout_seconds=1,
    )

    assert await watcher.reconcile() == 2
    snapshot = await pool.snapshot()
    assert snapshot.counts[SandboxState.READY] == 2
    assert provider.created == 2
