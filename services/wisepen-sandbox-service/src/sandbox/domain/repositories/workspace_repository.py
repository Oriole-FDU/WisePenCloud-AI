from __future__ import annotations

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

    async def set_export_bundle(
        self,
        workspace_id: str,
        export_bundle: WorkspaceExportBundleRef | None,
    ) -> SessionWorkspaceDocument | None:
        """更新 export_bundle，并返回更新后的记录"""
        ...

    async def change_state(
        self,
        workspace_id: str,
        state: WorkspaceState,
    ) -> SessionWorkspaceDocument | None:
        """更新 workspace 生命周期状态，并返回更新后的记录"""
        ...