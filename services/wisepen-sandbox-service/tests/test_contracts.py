from __future__ import annotations

from datetime import timedelta

import pytest

from sandbox.errors import (
    FencingRejectedError,
    InvalidStateTransition,
    LeaseConflictError,
    WorkspaceSyncError,
)
from sandbox.models import (
    ExecutionRequest,
    SandboxState,
    WorkspaceSnapshot,
    utc_now,
)
from sandbox.scheduler import SandboxScheduler
from sandbox.workspace import LocalWorkspaceStore, WorkspacePathError

from test_lifecycle import FakeProvider, FakeWorkspace, ready_pool


@pytest.mark.asyncio
async def test_request_id_is_idempotent_and_context_is_fenced():
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())

    first = await scheduler.allocate("req-1", "tenant", "workspace")
    second = await scheduler.allocate("req-1", "tenant", "workspace")
    assert second == first

    with pytest.raises(LeaseConflictError):
        await scheduler.allocate("req-1", "other-tenant", "workspace")
    with pytest.raises(FencingRejectedError):
        await scheduler.execute(
            first.lease_id,
            ExecutionRequest(
                "exec-1",
                "tenant",
                "workspace",
                "shell_exec",
                fencing_token=first.fencing_token - 1,
            ),
        )


@pytest.mark.asyncio
async def test_expired_lease_is_rejected_and_recovered():
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())
    lease = await scheduler.allocate("req-expired", "tenant", "workspace")
    record = await repository.find_lease(lease.lease_id)
    record.lease_expires_at = utc_now() - timedelta(seconds=1)

    with pytest.raises(Exception) as exc_info:
        await scheduler.execute(
            lease.lease_id,
            ExecutionRequest(
                "exec-1", "tenant", "workspace", "shell_exec", fencing_token=lease.fencing_token
            ),
        )
    assert exc_info.value.code == "LEASE_EXPIRED"
    assert await scheduler.recover_expired() == 1
    assert provider.destroyed == [lease.sandbox_id]


@pytest.mark.asyncio
async def test_release_is_idempotent_and_commit_failure_still_destroys():
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)

    class FailingWorkspace(FakeWorkspace):
        async def commit(self, snapshot, lease_id, fencing_token=0):
            raise RuntimeError("store unavailable")

    scheduler = SandboxScheduler(pool, repository, provider, FailingWorkspace())
    lease = await scheduler.allocate("req-release", "tenant", "workspace")
    with pytest.raises(WorkspaceSyncError):
        await scheduler.release(lease.lease_id, lease.fencing_token)
    await scheduler.release(lease.lease_id, lease.fencing_token)
    assert provider.destroyed == [lease.sandbox_id]
    assert (await scheduler.status(lease.sandbox_id)).state == SandboxState.DESTROYED


@pytest.mark.asyncio
async def test_state_machine_rejects_invalid_transition():
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    record = await repository.get("sb-1")
    assert record is not None
    with pytest.raises(InvalidStateTransition):
        await repository.transition("sb-1", SandboxState.READY, SandboxState.DESTROYED)


@pytest.mark.asyncio
async def test_workspace_store_rejects_traversal_and_symlink(tmp_path):
    store = LocalWorkspaceStore(str(tmp_path))
    with pytest.raises(WorkspacePathError):
        await store.commit(WorkspaceSnapshot("tenant", "workspace", {"../escape": "x"}), "lease", 1)
