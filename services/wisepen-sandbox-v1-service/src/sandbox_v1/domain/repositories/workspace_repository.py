from __future__ import annotations

from typing import Protocol

from sandbox_v1.domain.entities import (
    WorkspaceRecord,
    WorkspaceRestoreStart,
    WorkspaceSnapshotRef,
)


class WorkspaceRepository(Protocol):
    """Workspace 生命周期状态与 tombstone 快照指针的权威存储端口。"""

    async def get(self, user_id: str, session_id: str) -> WorkspaceRecord | None:
        """按用户和会话读取 Workspace 记录。"""
        ...

    async def ensure_active(
        self,
        *,
        user_id: str,
        session_id: str,
        workspace_key: str,
        workspace_path: str,
    ) -> WorkspaceRecord:
        """创建或激活 Workspace；已删除记录不能被隐式复活。"""
        ...

    async def begin_delete(
        self,
        *,
        user_id: str,
        session_id: str,
        workspace_key: str,
        workspace_path: str,
    ) -> WorkspaceRecord:
        """声明进入 DELETING，供快照和物理目录删除流程使用。"""
        ...

    async def finish_delete(
        self,
        *,
        user_id: str,
        session_id: str,
        snapshot: WorkspaceSnapshotRef | None,
    ) -> WorkspaceRecord:
        """提交逻辑删除结果和 tombstone 快照，落到 DELETED。"""
        ...

    async def remember_snapshot(
        self,
        *,
        user_id: str,
        session_id: str,
        snapshot: WorkspaceSnapshotRef,
    ) -> WorkspaceRecord:
        """更新可恢复快照指针，不改变 Workspace 生命周期状态。"""
        ...

    async def fail_delete(
        self,
        *,
        user_id: str,
        session_id: str,
        error: str,
    ) -> WorkspaceRecord:
        """删除失败时回滚状态并记录错误。"""
        ...

    async def begin_restore(
        self,
        *,
        user_id: str,
        session_id: str,
        workspace_key: str,
        workspace_path: str,
    ) -> WorkspaceRestoreStart:
        """以并发安全方式尝试抢占 RESTORING 状态。"""
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
        """提交 restore 结果并落到 ACTIVE。"""
        ...

    async def fail_restore(
        self,
        *,
        user_id: str,
        session_id: str,
        error: str,
    ) -> WorkspaceRecord:
        """恢复失败时回滚为 DELETED，并保留重试所需 tombstone。"""
        ...

    async def mark_snapshot_unrecoverable(
        self,
        snapshot: WorkspaceSnapshotRef,
        *,
        reason: str,
    ) -> None:
        """把引用指定快照的 tombstone 标记为不可恢复。"""
        ...
