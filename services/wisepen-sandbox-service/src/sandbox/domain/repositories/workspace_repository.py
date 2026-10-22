from __future__ import annotations

from typing import Protocol

from sandbox.domain.entities import SessionWorkspaceDocument


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
        """写入运行时关联，不更新 session 实际使用时间。"""
        ...

    async def list_by_sandbox(
        self,
        sandbox_id: str,
    ) -> list[SessionWorkspaceDocument]:
        """读取沙箱中有运行时目录的 session 工作区"""
        ...