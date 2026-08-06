from __future__ import annotations

from typing import Protocol

from sandbox_v1.domain.entities import (
    WorkspaceRecord,
    WorkspaceRestoreStart,
    WorkspaceSnapshotRef,
)


class WorkspaceRepository(Protocol):
    """Authority boundary for Workspace lifecycle state and tombstones."""

    async def get(self, user_id: str, session_id: str) -> WorkspaceRecord | None:
        ...

    async def ensure_active(
        self,
        *,
        user_id: str,
        session_id: str,
        workspace_key: str,
        workspace_path: str,
    ) -> WorkspaceRecord:
        ...

    async def begin_delete(
        self,
        *,
        user_id: str,
        session_id: str,
        workspace_key: str,
        workspace_path: str,
    ) -> WorkspaceRecord:
        ...

    async def finish_delete(
        self,
        *,
        user_id: str,
        session_id: str,
        snapshot: WorkspaceSnapshotRef | None,
    ) -> WorkspaceRecord:
        ...

    async def remember_snapshot(
        self,
        *,
        user_id: str,
        session_id: str,
        snapshot: WorkspaceSnapshotRef,
    ) -> WorkspaceRecord:
        ...

    async def fail_delete(
        self,
        *,
        user_id: str,
        session_id: str,
        error: str,
    ) -> WorkspaceRecord:
        ...

    async def begin_restore(
        self,
        *,
        user_id: str,
        session_id: str,
        workspace_key: str,
        workspace_path: str,
    ) -> WorkspaceRestoreStart:
        ...

    async def finish_restore(
        self,
        *,
        user_id: str,
        session_id: str,
        restored_from_snapshot: bool,
        snapshot: WorkspaceSnapshotRef | None,
        unrecoverable_reason: str | None = None,
    ) -> WorkspaceRecord:
        ...

    async def fail_restore(
        self,
        *,
        user_id: str,
        session_id: str,
        error: str,
    ) -> WorkspaceRecord:
        ...

    async def mark_snapshot_unrecoverable(
        self,
        snapshot: WorkspaceSnapshotRef,
        *,
        reason: str,
    ) -> None:
        ...
