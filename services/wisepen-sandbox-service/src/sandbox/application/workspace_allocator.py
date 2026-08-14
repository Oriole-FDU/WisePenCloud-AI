from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sandbox.application.container_manager import ContainerManager
from sandbox.domain.entities import (
    SandboxState,
    SessionWorkspaceDocument,
    WorkspaceState,
)
from sandbox.domain.repositories import SandboxRepository, WorkspaceRepository

class WorkspaceAllocator:
    """协调用户沙箱与会话工作区的分配。"""

    def __init__(
        self,
        sandbox_repository: SandboxRepository,
        workspace_repository: WorkspaceRepository,
        container_manager: ContainerManager,
    ) -> None:
        self._sandbox_repository = sandbox_repository
        self._workspace_repository = workspace_repository
        self._container_manager = container_manager

    async def allocate(self, user_id: str, session_id: str) -> str:
        workspace = await self._workspace_repository.get_by_user_session(user_id, session_id)
        if workspace and workspace.state == WorkspaceState.ATTACHED and workspace.sandbox_id and workspace.workspace_path:
            return workspace.id

        sandbox = await self._sandbox_repository.get_by_user_binding(user_id)
        if sandbox is None or sandbox.state != SandboxState.USER_ACTIVE:
            sandbox = await self._sandbox_repository.assign_to_user(user_id)

        if (
            workspace
            and workspace.state == WorkspaceState.DETACHED
            and workspace.export_bundle and workspace.export_bundle.bundle_path
            and Path(workspace.export_bundle.bundle_path).is_dir()
        ):
            workspace_path = await self._container_manager.restore_cached_workspace(sandbox.container_id, workspace.id)
            await self._workspace_repository.set_attached_workspace(
                workspace.id,
                sandbox.sandbox_id,
                workspace_path,
            )
            return workspace.id

        if workspace is not None:
            await self._workspace_repository.change_state(workspace.id, WorkspaceState.LOST)

        workspace_id = uuid4().hex
        workspace_path = await self._container_manager.create_workspace_directory(
            sandbox.container_id,
            workspace_id,
        )
        await self._workspace_repository.save(
            SessionWorkspaceDocument(
                id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                state=WorkspaceState.ATTACHED,
                sandbox_id=sandbox.sandbox_id,
                workspace_path=workspace_path,
            )
        )
        return workspace_id
