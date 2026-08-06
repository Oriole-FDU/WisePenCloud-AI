from __future__ import annotations

from pathlib import Path
from typing import Protocol

from sandbox_v1.domain.entities import (
    WorkspaceEvictionReason,
    WorkspaceRestoreOutcome,
    WorkspaceSnapshotRef,
)


class WorkspaceCache(Protocol):
    """Host-side cache used before recycle or Chat-initiated logical delete."""

    async def snapshot(
        self,
        *,
        workspace_key: str,
        user_id: str,
        session_id: str,
        source_path: Path,
    ) -> WorkspaceSnapshotRef | None:
        ...

    async def restore(
        self,
        snapshot: WorkspaceSnapshotRef | None,
        *,
        target_path: Path,
    ) -> WorkspaceRestoreOutcome:
        ...

    async def evict_expired(self) -> list[WorkspaceSnapshotRef]:
        ...

    async def evict_lru(self) -> list[WorkspaceSnapshotRef]:
        ...

    async def mark_unrecoverable(
        self,
        snapshot: WorkspaceSnapshotRef,
        reason: WorkspaceEvictionReason,
    ) -> WorkspaceSnapshotRef:
        ...
