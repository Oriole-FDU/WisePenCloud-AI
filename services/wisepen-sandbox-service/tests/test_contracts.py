from __future__ import annotations

import json
from datetime import timedelta

import pytest
from common.core.exceptions import ServiceException

from sandbox.application.services import SandboxPool, SandboxScheduler
from sandbox.core.storage.local import LocalWorkspaceStore
from sandbox.core.storage.memory import MemorySandboxRepository
from sandbox.domain.entities import ExecutionRequest, SandboxRecord, SandboxRef, SandboxState, WorkspaceSnapshot, utc_now
from sandbox.domain.error_codes import SandboxErrorCode
from test_lifecycle import FakeProvider, FakeWorkspace, ready_pool


@pytest.mark.asyncio
async def test_request_id_is_idempotent_and_context_is_fenced() -> None:
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())
    first = await scheduler.allocate("req-1", "user", "session")
    assert await scheduler.allocate("req-1", "user", "session") == first

    with pytest.raises(ServiceException) as exc_info:
        await scheduler.allocate("req-1", "other-user", "session")
    assert exc_info.value.code == SandboxErrorCode.REQUEST_CONFLICT.code
    with pytest.raises(ServiceException) as exc_info:
        await scheduler.execute(
            first.lease_id,
            ExecutionRequest("exec", "user", "session", "shell_exec", fencing_token=first.fencing_token - 1),
        )
    assert exc_info.value.code == SandboxErrorCode.FENCING_REJECTED.code


@pytest.mark.asyncio
async def test_released_request_id_cannot_reopen_a_closed_turn() -> None:
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())
    lease = await scheduler.allocate("req-released", "user", "session")
    await scheduler.release(lease.lease_id, lease.fencing_token)

    with pytest.raises(ServiceException) as exc_info:
        await scheduler.allocate("req-released", "user", "session")
    assert exc_info.value.code == SandboxErrorCode.LEASE_EXPIRED.code


@pytest.mark.asyncio
async def test_expired_turn_is_released_without_destroying_user_container() -> None:
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())
    lease = await scheduler.allocate("expired", "user", "session")
    turn = await repository.get_turn_lease(lease.lease_id)
    turn.expires_at = utc_now() - timedelta(seconds=1)

    with pytest.raises(ServiceException) as exc_info:
        await scheduler.execute(
            lease.lease_id,
            ExecutionRequest("exec", "user", "session", "shell_exec", fencing_token=lease.fencing_token),
        )
    assert exc_info.value.code == SandboxErrorCode.LEASE_EXPIRED.code
    assert await scheduler.recover_expired() == 1
    assert provider.destroyed == []
    assert (await scheduler.status(lease.sandbox_id)).state == SandboxState.USER_IDLE


@pytest.mark.asyncio
async def test_state_machine_rejects_invalid_transition() -> None:
    provider = FakeProvider()
    repository, _ = await ready_pool(provider)
    with pytest.raises(ServiceException) as exc_info:
        await repository.transition("sb-1", SandboxState.READY, SandboxState.DESTROYED)
    assert exc_info.value.code == SandboxErrorCode.INVALID_STATE_TRANSITION.code


@pytest.mark.asyncio
async def test_workspace_store_rejects_traversal_and_supports_delete(tmp_path) -> None:
    store = LocalWorkspaceStore(str(tmp_path))
    with pytest.raises(ServiceException) as exc_info:
        await store.commit(WorkspaceSnapshot("user", "session", {"../escape": "x"}), "lease", 1)
    assert exc_info.value.code == SandboxErrorCode.WORKSPACE_PATH_INVALID.code

    await store.commit(WorkspaceSnapshot("user", "session", {"a.txt": "a"}), "lease", 1)
    await store.delete("user", "session")
    assert (await store.snapshot("user", "session")).files == {}


@pytest.mark.asyncio
async def test_workspace_commit_replaces_previous_snapshot(tmp_path) -> None:
    store = LocalWorkspaceStore(str(tmp_path))
    await store.commit(WorkspaceSnapshot("user", "session", {"old.txt": "old", "keep.txt": "v1"}), "lease-1", 1)
    await store.commit(WorkspaceSnapshot("user", "session", {"keep.txt": "v2", "new.txt": "new"}), "lease-2", 2)
    snapshot = await store.snapshot("user", "session")
    assert snapshot.files == {"keep.txt": "v2", "new.txt": "new"}


@pytest.mark.asyncio
async def test_workspace_manifest_is_not_restored(tmp_path) -> None:
    store = LocalWorkspaceStore(str(tmp_path))
    await store.commit(WorkspaceSnapshot("user", "session", {"dir/main.py": "print(1)"}), "lease-1", 7)
    snapshot = await store.snapshot("user", "session")
    manifest = json.loads((tmp_path / "user" / "session" / ".wisepen-workspace-manifest.json").read_text())
    assert snapshot.files == {"dir/main.py": "print(1)"}
    assert manifest["fencing_token"] == 7


@pytest.mark.asyncio
async def test_return_ready_requires_token_and_rejects_bound_container() -> None:
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository)
    record = SandboxRecord(SandboxRef("warming", "provider"), SandboxState.WARMING)
    await repository.save(record)
    token, generation = await pool.prepare_readiness(record)
    with pytest.raises(ServiceException):
        await pool.return_ready("warming", "wrong", generation)
    await pool.return_ready("warming", token, generation)

    _, lease = await pool.checkout("req", "user", "session")
    record = await repository.get(lease.sandbox_id)
    assert record is not None and record.owner_user_id == "user"


@pytest.mark.asyncio
async def test_metrics_expose_user_binding_and_turn_fields() -> None:
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository, min_ready=2, target_ready=3)
    metrics = (await pool.snapshot()).as_dict()
    assert metrics["min_ready"] == 2
    assert metrics["target_ready"] == 3
    assert "active_user_bindings" in metrics
    assert "idle_user_bindings" in metrics
    assert "active_turn_leases" in metrics
