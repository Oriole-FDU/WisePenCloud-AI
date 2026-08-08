from __future__ import annotations

from pathlib import Path
from typing import Protocol

from sandbox_v1.domain.entities import (
    WorkspaceEvictionReason,
    WorkspaceRestoreOutcome,
    WorkspaceSnapshotRef,
)


class WorkspaceCache(Protocol):
    """回收或 Chat 逻辑删除前使用的 host-side Workspace 快照缓存端口。"""

    async def snapshot(
        self,
        *,
        workspace_key: str,
        user_id: str,
        session_id: str,
        source_path: Path,
    ) -> WorkspaceSnapshotRef | None:
        """为 source_path 创建快照；源目录不存在时可返回 None。"""
        ...

    async def restore(
        self,
        snapshot: WorkspaceSnapshotRef | None,
        *,
        target_path: Path,
    ) -> WorkspaceRestoreOutcome:
        """把快照恢复到 target_path；快照缺失时创建空 Workspace。"""
        ...

    async def evict_expired(self) -> list[WorkspaceSnapshotRef]:
        """淘汰超过 TTL 的快照，并返回被标记不可恢复的引用。"""
        ...

    async def evict_lru(self) -> list[WorkspaceSnapshotRef]:
        """按 LRU 淘汰超过容量水位的快照。"""
        ...

    async def mark_unrecoverable(
        self,
        snapshot: WorkspaceSnapshotRef,
        reason: WorkspaceEvictionReason,
    ) -> WorkspaceSnapshotRef:
        """把指定快照标记为不可恢复，并保留原因。"""
        ...
