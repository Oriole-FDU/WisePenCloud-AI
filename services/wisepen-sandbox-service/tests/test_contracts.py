from __future__ import annotations

import json
from datetime import timedelta

import pytest
from common.core.exceptions import ServiceException

from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.domain.entities import (
    ExecutionRequest,
    SandboxRecord,
    SandboxRef,
    SandboxSpec,
    SandboxState,
    WorkspaceSnapshot,
    utc_now,
)
from sandbox.application.services import SandboxPool, SandboxScheduler, Watcher
from sandbox.core.storage.local import LocalWorkspaceStore
from sandbox.core.storage.memory import MemorySandboxRepository

from test_lifecycle import FakeProvider, FakeWorkspace, ready_pool


@pytest.mark.asyncio
async def test_request_id_is_idempotent_and_context_is_fenced():
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())

    first = await scheduler.allocate("req-1", "tenant", "workspace")
    second = await scheduler.allocate("req-1", "tenant", "workspace")
    assert second == first

    with pytest.raises(ServiceException) as exc_info:
        await scheduler.allocate("req-1", "other-tenant", "workspace")
    assert exc_info.value.code == SandboxErrorCode.REQUEST_CONFLICT.code
    with pytest.raises(ServiceException) as exc_info:
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
    assert exc_info.value.code == SandboxErrorCode.FENCING_REJECTED.code


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
    assert exc_info.value.code == SandboxErrorCode.LEASE_EXPIRED.code
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
    with pytest.raises(ServiceException) as exc_info:
        await scheduler.release(lease.lease_id, lease.fencing_token)
    assert exc_info.value.code == SandboxErrorCode.WORKSPACE_SYNC_FAILED.code
    await scheduler.release(lease.lease_id, lease.fencing_token)
    assert provider.destroyed == [lease.sandbox_id]
    assert (await scheduler.status(lease.sandbox_id)).state == SandboxState.DESTROYED


@pytest.mark.asyncio
async def test_state_machine_rejects_invalid_transition():
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    record = await repository.get("sb-1")
    assert record is not None
    with pytest.raises(ServiceException) as exc_info:
        await repository.transition("sb-1", SandboxState.READY, SandboxState.DESTROYED)
    assert exc_info.value.code == SandboxErrorCode.INVALID_STATE_TRANSITION.code


@pytest.mark.asyncio
async def test_workspace_store_rejects_traversal_and_symlink(tmp_path):
    store = LocalWorkspaceStore(str(tmp_path))
    with pytest.raises(ServiceException) as exc_info:
        await store.commit(WorkspaceSnapshot("tenant", "workspace", {"../escape": "x"}), "lease", 1)
    assert exc_info.value.code == SandboxErrorCode.WORKSPACE_PATH_INVALID.code


@pytest.mark.asyncio
async def test_workspace_commit_replaces_previous_snapshot(tmp_path):
    store = LocalWorkspaceStore(str(tmp_path))
    await store.commit(
        WorkspaceSnapshot("tenant", "workspace", {"old.txt": "old", "keep.txt": "v1"}),
        "lease-1",
        1,
    )
    await store.commit(
        WorkspaceSnapshot("tenant", "workspace", {"keep.txt": "v2", "new.txt": "new"}),
        "lease-2",
        2,
    )

    root = tmp_path / "tenant" / "workspace"
    assert not (root / "old.txt").exists()
    assert (root / "keep.txt").read_text(encoding="utf-8") == "v2"
    assert (root / "new.txt").read_text(encoding="utf-8") == "new"


@pytest.mark.asyncio
async def test_workspace_snapshot_restores_committed_files_without_manifest(tmp_path):
    store = LocalWorkspaceStore(str(tmp_path))
    await store.commit(
        WorkspaceSnapshot("tenant", "workspace", {"dir/main.py": "print(1)"}),
        "lease-1",
        7,
    )

    snapshot = await store.snapshot("tenant", "workspace")
    manifest = json.loads(
        (tmp_path / "tenant" / "workspace" / ".wisepen-workspace-manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert snapshot.files == {"dir/main.py": "print(1)"}
    assert manifest["lease_id"] == "lease-1"
    assert manifest["fencing_token"] == 7


@pytest.mark.asyncio
async def test_workspace_commit_is_atomic_on_invalid_snapshot(tmp_path):
    store = LocalWorkspaceStore(str(tmp_path))
    await store.commit(WorkspaceSnapshot("tenant", "workspace", {"keep.txt": "old"}), "lease-1", 1)

    with pytest.raises(ServiceException) as exc_info:
        await store.commit(
            WorkspaceSnapshot("tenant", "workspace", {"../escape.txt": "bad"}),
            "lease-2",
            2,
        )

    assert exc_info.value.code == SandboxErrorCode.WORKSPACE_PATH_INVALID.code
    snapshot = await store.snapshot("tenant", "workspace")
    assert snapshot.files == {"keep.txt": "old"}


@pytest.mark.asyncio
async def test_workspace_cache_limits_are_enforced(tmp_path):
    store = LocalWorkspaceStore(str(tmp_path), max_files=1)
    with pytest.raises(ServiceException) as exc_info:
        await store.commit(
            WorkspaceSnapshot("tenant", "workspace", {"a.txt": "a", "b.txt": "b"}),
            "lease-1",
            1,
        )
    assert exc_info.value.code == SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED.code

    store = LocalWorkspaceStore(str(tmp_path), max_file_bytes=3)
    with pytest.raises(ServiceException) as exc_info:
        await store.commit(WorkspaceSnapshot("tenant", "workspace", {"a.txt": "abcd"}), "lease-1", 1)
    assert exc_info.value.code == SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED.code

    store = LocalWorkspaceStore(str(tmp_path), max_files=10, max_total_bytes=5)
    with pytest.raises(ServiceException) as exc_info:
        await store.commit(
            WorkspaceSnapshot("tenant", "workspace", {"a.txt": "abc", "b.txt": "def"}),
            "lease-1",
            1,
        )
    assert exc_info.value.code == SandboxErrorCode.WORKSPACE_CACHE_LIMIT_EXCEEDED.code


@pytest.mark.asyncio
async def test_return_ready_requires_health_token_and_current_generation():
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository)
    record = SandboxRecord(SandboxRef("warming", "provider"), SandboxState.WARMING)
    await repository.save(record)
    token, generation = await pool.prepare_readiness(record)

    with pytest.raises(ServiceException) as exc_info:
        await pool.return_ready(record.ref.sandbox_id, "wrong", generation)
    assert exc_info.value.code == SandboxErrorCode.FENCING_REJECTED.code
    assert (await repository.get(record.ref.sandbox_id)).state == SandboxState.WARMING

    await pool.return_ready(record.ref.sandbox_id, token, generation)
    assert (await repository.get(record.ref.sandbox_id)).state == SandboxState.READY


@pytest.mark.asyncio
async def test_return_ready_rejects_active_lease():
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository)
    record = SandboxRecord(
        SandboxRef("warming-leased", "provider"),
        SandboxState.WARMING,
        lease_id="lease-1",
        request_id="request-1",
        tenant_id="tenant",
        workspace_id="workspace",
    )
    await repository.save(record)
    token, generation = await pool.prepare_readiness(record)

    with pytest.raises(ServiceException) as exc_info:
        await pool.return_ready("warming-leased", token, generation)
    assert exc_info.value.code == SandboxErrorCode.FENCING_REJECTED.code


@pytest.mark.asyncio
async def test_watcher_recovers_expired_lease_before_replenishing():
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())
    lease = await scheduler.allocate("expired-watcher", "tenant", "workspace")
    record = await repository.find_lease(lease.lease_id)
    record.lease_expires_at = utc_now() - timedelta(seconds=1)

    watcher = Watcher(
        pool,
        repository,
        provider,
        SandboxSpec("test"),
        scheduler=scheduler,
        target_ready=1,
        min_ready=1,
    )
    await watcher.reconcile()

    assert provider.destroyed == [lease.sandbox_id]
    assert (await scheduler.status(lease.sandbox_id)).state == SandboxState.DESTROYED
    assert (await pool.snapshot()).counts[SandboxState.READY] == 1


@pytest.mark.asyncio
async def test_metrics_expose_readiness_and_lifecycle_fields():
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository, min_ready=2, target_ready=3)
    metrics = (await pool.snapshot()).as_dict()
    assert metrics["ready_count"] == 0
    assert metrics["min_ready"] == 2
    assert metrics["target_ready"] == 3
    assert "warmup_failure_rate" in metrics
    assert "destroy_failure_rate" in metrics
    assert "active_leases_by_tenant" in metrics
    assert "zombie_leases" in metrics
