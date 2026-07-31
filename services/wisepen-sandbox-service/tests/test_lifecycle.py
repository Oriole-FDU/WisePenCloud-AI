from __future__ import annotations

import asyncio

import pytest
from common.core.exceptions import ServiceException

from sandbox.domain.entities import (
    Endpoint,
    ExecutionRequest,
    ExecutionResult,
    SandboxRecord,
    SandboxRef,
    SandboxSpec,
    SandboxState,
    WorkspaceSnapshot,
    Health,
    utc_now,
)
from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.application.services import SandboxPool, SandboxScheduler, Watcher
from sandbox.core.storage.local import LocalWorkspaceStore
from sandbox.core.storage.memory import MemorySandboxRepository


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
        self.prepared_snapshots: list[WorkspaceSnapshot] = []
        self.exported_files: dict[str, str] = {"result.txt": "done"}
        self.fail_prepare = False
        self.checkpoints: list[tuple[str, int]] = []

    async def validate_deployment(self) -> None:
        return None

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
        self.prepared_snapshots.append(workspace)
        if self.fail_prepare:
            raise RuntimeError("prepare failed")

    async def activate(self, sandbox: SandboxRef, lease) -> Endpoint:
        return sandbox.endpoint

    async def forward(self, sandbox: SandboxRef, request: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(request.request_id, "succeeded", {"ok": True})

    async def export_workspace(self, sandbox: SandboxRef, tenant_id: str, workspace_id: str) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(tenant_id, workspace_id, dict(self.exported_files))

    async def checkpoint_workspace(
        self,
        sandbox: SandboxRef,
        tenant_id: str,
        workspace_id: str,
        lease_id: str,
        fencing_token: int,
    ) -> WorkspaceSnapshot:
        self.checkpoints.append((lease_id, fencing_token))
        return WorkspaceSnapshot(tenant_id, workspace_id, dict(self.exported_files))

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        self.destroyed.append(sandbox.sandbox_id)


async def ready_pool(provider: FakeProvider):
    repository = MemorySandboxRepository()
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
async def test_expired_lease_exports_cache_before_destroying():
    events: list[str] = []

    class RecordingWorkspace(FakeWorkspace):
        async def commit(self, snapshot, lease_id, fencing_token=0):
            events.append("commit")
            await super().commit(snapshot, lease_id, fencing_token)

    class RecordingProvider(FakeProvider):
        async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
            events.append("destroy")
            await super().destroy(sandbox, reason)

    provider = RecordingProvider()
    repository, pool = await ready_pool(provider)
    workspace = RecordingWorkspace()
    scheduler = SandboxScheduler(pool, repository, provider, workspace)
    lease = await scheduler.allocate("req-expired-cache", "tenant", "workspace")
    record = await repository.find_lease(lease.lease_id)
    record.lease_expires_at = utc_now()

    assert await scheduler.recover_expired() == 1

    assert events == ["commit", "destroy"]
    assert workspace.commits[0][0].files == {"result.txt": "done"}
    assert provider.destroyed == [lease.sandbox_id]


@pytest.mark.asyncio
async def test_checkpoint_validates_fencing_and_commits_without_destroying():
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    workspace = FakeWorkspace()
    scheduler = SandboxScheduler(pool, repository, provider, workspace)
    lease = await scheduler.allocate("req-checkpoint", "tenant", "workspace")

    await scheduler.checkpoint(lease.lease_id, lease.fencing_token)

    assert provider.checkpoints == [(lease.lease_id, lease.fencing_token)]
    assert workspace.commits[-1][0].files == {"result.txt": "done"}
    assert provider.destroyed == []
    with pytest.raises(ServiceException) as exc_info:
        await scheduler.checkpoint(lease.lease_id, lease.fencing_token + 1)
    assert exc_info.value.code == SandboxErrorCode.FENCING_REJECTED.code


@pytest.mark.asyncio
async def test_shutdown_commits_active_workspace_before_destroying():
    events: list[str] = []

    class RecordingWorkspace(FakeWorkspace):
        async def commit(self, snapshot, lease_id, fencing_token=0):
            events.append("commit")
            await super().commit(snapshot, lease_id, fencing_token)

    class RecordingProvider(FakeProvider):
        async def destroy(self, sandbox, reason):
            events.append("destroy")
            await super().destroy(sandbox, reason)

    provider = RecordingProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, RecordingWorkspace())
    await scheduler.allocate("req-shutdown", "tenant", "workspace")

    assert await scheduler.shutdown() == []
    assert events == ["commit", "destroy"]


@pytest.mark.asyncio
async def test_expired_lease_destroy_continues_when_commit_fails():
    class FailingWorkspace(FakeWorkspace):
        async def commit(self, snapshot, lease_id, fencing_token=0):
            raise RuntimeError("store unavailable")

    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FailingWorkspace())
    lease = await scheduler.allocate("req-expired-failing-cache", "tenant", "workspace")
    record = await repository.find_lease(lease.lease_id)
    record.lease_expires_at = utc_now()

    assert await scheduler.recover_expired() == 1
    assert provider.destroyed == [lease.sandbox_id]
    assert (await scheduler.status(lease.sandbox_id)).state == SandboxState.DESTROYED


@pytest.mark.asyncio
async def test_next_allocate_prepares_cached_workspace(tmp_path):
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    store = LocalWorkspaceStore(str(tmp_path))
    scheduler = SandboxScheduler(pool, repository, provider, store)
    provider.exported_files = {"cached.txt": "value"}

    first = await scheduler.allocate("req-cache-first", "tenant", "workspace")
    await scheduler.release(first.lease_id, first.fencing_token)

    second_record = SandboxRecord(
        ref=await provider.create(SandboxSpec("test")),
        state=SandboxState.WARMING,
    )
    await pool.add_ready(second_record)
    await scheduler.allocate("req-cache-second", "tenant", "workspace")

    assert provider.prepared_snapshots[-1].files == {"cached.txt": "value"}


@pytest.mark.asyncio
async def test_allocation_failure_destroys_sandbox():
    provider = FakeProvider()
    provider.fail_prepare = True
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())

    with pytest.raises(ServiceException) as exc_info:
        await scheduler.allocate("req-2", "tenant", "workspace")
    assert exc_info.value.code == SandboxErrorCode.SANDBOX_UNAVAILABLE.code
    assert provider.destroyed


@pytest.mark.asyncio
async def test_watcher_fills_only_the_ready_deficit():
    provider = FakeProvider()
    repository = MemorySandboxRepository()
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


@pytest.mark.asyncio
async def test_watcher_checkpoints_active_leases():
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    workspace = FakeWorkspace()
    scheduler = SandboxScheduler(pool, repository, provider, workspace)
    lease = await scheduler.allocate("req-watcher-checkpoint", "tenant", "workspace")
    watcher = Watcher(
        pool,
        repository,
        provider,
        SandboxSpec("test"),
        scheduler=scheduler,
        target_ready=0,
        checkpoint_interval_seconds=1,
    )
    watcher._next_checkpoint_at = 0

    await watcher.reconcile()

    assert provider.checkpoints == [(lease.lease_id, lease.fencing_token)]
