from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sandbox_v1.application.services.workspace_service import WorkspaceService
from sandbox_v1.core.observability import MetricsCollector
from sandbox_v1.core.storage.filesystem import LocalWorkspaceSnapshotCache
from sandbox_v1.core.storage.memory import MemoryWorkspaceRepository
from sandbox_v1.domain.entities import (
    WorkspaceEvictionReason,
    WorkspaceLifecycleStatus,
    WorkspaceRestoreOutcome,
    WorkspaceSnapshotRef,
)


def _service(tmp_path: Path) -> WorkspaceService:
    return WorkspaceService(
        repository=MemoryWorkspaceRepository(),
        cache=LocalWorkspaceSnapshotCache(cache_root=tmp_path / "cache"),
        workspace_root=tmp_path / "workspaces",
        metrics=MetricsCollector(),
    )


@pytest.mark.asyncio
async def test_logical_delete_keeps_tombstone_and_rebuild_restores(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workspace_path = (
        tmp_path
        / "workspaces"
        / service.workspace_key("user-a", "session-a")
    )
    workspace_path.mkdir(parents=True)
    (workspace_path / "answer.txt").write_text("42", encoding="utf-8")

    deleted = await service.logical_delete(
        user_id="user-a",
        session_id="session-a",
    )
    assert deleted.status == WorkspaceLifecycleStatus.WORKSPACE_DELETED
    assert deleted.snapshot_id is not None
    assert not workspace_path.exists()

    late_access = await service.ensure_active(
        user_id="user-a",
        session_id="session-a",
    )
    assert late_access.status == WorkspaceLifecycleStatus.WORKSPACE_DELETED
    assert not workspace_path.exists()

    rebuilt = await service.rebuild(
        user_id="user-a",
        session_id="session-a",
    )
    assert rebuilt.status == WorkspaceLifecycleStatus.WORKSPACE_READY
    assert rebuilt.restored_from_snapshot is True
    assert (workspace_path / "answer.txt").read_text(encoding="utf-8") == "42"


@pytest.mark.asyncio
async def test_rebuild_without_snapshot_creates_empty_workspace(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workspace_path = (
        tmp_path
        / "workspaces"
        / service.workspace_key("user-b", "session-b")
    )

    rebuilt = await service.rebuild(
        user_id="user-b",
        session_id="session-b",
    )

    assert rebuilt.status == WorkspaceLifecycleStatus.WORKSPACE_READY
    assert rebuilt.restored_from_snapshot is False
    assert rebuilt.unrecoverable_reason == "snapshot_missing"
    assert workspace_path.exists()
    assert list(workspace_path.iterdir()) == []


class SlowRestoreCache:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def snapshot(
        self,
        *,
        workspace_key: str,
        user_id: str,
        session_id: str,
        source_path: Path,
    ) -> WorkspaceSnapshotRef | None:
        return None

    async def restore(
        self,
        snapshot: WorkspaceSnapshotRef | None,
        *,
        target_path: Path,
    ) -> WorkspaceRestoreOutcome:
        self.started.set()
        await self.release.wait()
        target_path.mkdir(parents=True, exist_ok=True)
        return WorkspaceRestoreOutcome(restored_from_snapshot=False)

    async def evict_expired(self) -> list[WorkspaceSnapshotRef]:
        return []

    async def evict_lru(self) -> list[WorkspaceSnapshotRef]:
        return []

    async def mark_unrecoverable(
        self,
        snapshot: WorkspaceSnapshotRef,
        reason: WorkspaceEvictionReason,
    ) -> WorkspaceSnapshotRef:
        return snapshot


@pytest.mark.asyncio
async def test_concurrent_rebuild_returns_workspace_restoring(tmp_path: Path) -> None:
    cache = SlowRestoreCache()
    service = WorkspaceService(
        repository=MemoryWorkspaceRepository(),
        cache=cache,
        workspace_root=tmp_path / "workspaces",
        metrics=MetricsCollector(),
    )

    first = asyncio.create_task(
        service.rebuild(user_id="user-c", session_id="session-c")
    )
    await cache.started.wait()

    second = await service.rebuild(user_id="user-c", session_id="session-c")
    assert second.status == WorkspaceLifecycleStatus.WORKSPACE_RESTORING

    cache.release.set()
    finished = await first
    assert finished.status == WorkspaceLifecycleStatus.WORKSPACE_READY
