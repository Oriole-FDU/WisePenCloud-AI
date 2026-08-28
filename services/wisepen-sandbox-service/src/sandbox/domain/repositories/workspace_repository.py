from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sandbox.domain.entities import (
    SessionWorkspaceDocument,
    WorkspaceExportBundleRef,
    WorkspaceState,
)


class WorkspaceRepository(Protocol):
    """SessionWorkspaceDocument 的 Mongo 权威仓储端口"""

    async def save(self, workspace: SessionWorkspaceDocument) -> None:
        """保存或覆盖一条 workspace 记录"""
        ...

    async def get_by_user_session(
        self,
        user_id: str,
        session_id: str,
    ) -> SessionWorkspaceDocument | None:
        """按 user_id 和 session_id 读取 workspace 记录"""
        ...

    async def get_by_id(
        self,
        workspace_id: str,
    ) -> SessionWorkspaceDocument | None:
        """按 workspace id 读取 workspace 记录"""
        ...

    async def set_new_workspace_path(
        self,
        workspace_id: str,
        workspace_path: str,
    ) -> SessionWorkspaceDocument | None:
        """更新 workspace_path，并返回更新后的记录"""
        ...

    async def set_attached_workspace(
        self,
        workspace_id: str,
        sandbox_id: str,
        workspace_path: str,
    ) -> SessionWorkspaceDocument | None:
        """写入运行时沙箱关联和容器内工作区路径。"""
        ...

    async def list_attached_by_sandbox(
        self,
        sandbox_id: str,
    ) -> list[SessionWorkspaceDocument]: ...

    async def list_idle_attached(
        self,
        cutoff: datetime,
        limit: int,
    ) -> list[SessionWorkspaceDocument]:
        """查询最近访问时间早于截止时间且仍在容器中的工作区。"""
        ...

    async def touch_if_attached(
        self,
        workspace_id: str,
    ) -> SessionWorkspaceDocument | None:
        """仅在工作区仍处于 ATTACHED 时更新最近访问时间。"""
        ...

    async def count_runtime_workspaces(
        self,
        sandbox_id: str,
    ) -> int:
        """统计仍占用沙箱运行时的工作区数量。"""
        ...

    async def change_state(
        self,
        workspace_id: str,
        state: WorkspaceState,
        *,
        export_bundle: WorkspaceExportBundleRef | None = None,
        clear_runtime_binding: bool = False,
    ) -> SessionWorkspaceDocument | None:
        """更新 workspace 状态，可选地写入缓存引用或清理运行时绑定。"""
        ...
