from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from common.core.exceptions import ServiceException
from pymongo.errors import DuplicateKeyError

from sandbox.application.container_manager import ContainerManager
from sandbox.domain.entities import (
    SandboxState,
    SessionWorkspaceDocument,
    WorkspaceState,
)
from sandbox.domain.error_codes import SandboxErrorCode
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
            sandbox = await self._sandbox_repository.get_by_id(workspace.sandbox_id)
            if sandbox is None or sandbox.state != SandboxState.USER_ACTIVE:
                raise ServiceException(SandboxErrorCode.SANDBOX_UNLOADING)
            if await self._sandbox_repository.start_session(workspace.sandbox_id, session_id) is not None:
                return workspace.id
            raise ServiceException(SandboxErrorCode.SANDBOX_UNLOADING)

        sandbox = await self._sandbox_repository.get_by_user_binding(user_id)
        if sandbox is not None and sandbox.state == SandboxState.USER_ACTIVE:
            if await self._sandbox_repository.start_session(sandbox.sandbox_id, session_id) is None:
                # 活动沙箱可能因 watcher 的并发判断进入 RETIRING 状态，导致会话启动失败，此时需重新分配沙箱
                sandbox = None

        if sandbox is None or sandbox.state != SandboxState.USER_ACTIVE:
            try:
                sandbox = await self._sandbox_repository.assign_to_user(user_id, session_id)
            except DuplicateKeyError:
                # 唯一键冲突时复用并发请求刚创建的活动沙箱
                sandbox = await self._sandbox_repository.get_by_user_binding(user_id)
                if sandbox is None:
                    raise
                if await self._sandbox_repository.start_session(sandbox.sandbox_id, session_id) is None:
                    raise ServiceException(SandboxErrorCode.SANDBOX_UNLOADING)
        # 工作区有导出过快照时，从中恢复
        if (
            workspace
            and workspace.state == WorkspaceState.DETACHED
            and workspace.export_bundle and workspace.export_bundle.bundle_path
            and Path(workspace.export_bundle.bundle_path).is_dir()
        ):
            try:
                workspace_path = await self._container_manager.restore_cached_workspace(sandbox.container_id, workspace.id, workspace.export_bundle.bundle_path)
                attached = await self._workspace_repository.set_attached_workspace(workspace.id, sandbox.sandbox_id, workspace_path)
                if attached is None:
                    raise ServiceException(SandboxErrorCode.SANDBOX_UNLOADING)
                return workspace.id
            except Exception:
                await self._sandbox_repository.finish_session(sandbox.sandbox_id, session_id)

        if workspace is not None:
            await self._workspace_repository.change_state(workspace.id, WorkspaceState.LOST)

        workspace_id = uuid4().hex
        try:
            workspace_path = await self._container_manager.create_workspace_directory(sandbox.container_id, workspace_id)
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
        except Exception:
            await self._sandbox_repository.finish_session(sandbox.sandbox_id, session_id)
            raise

    async def release(self, user_id: str, session_id: str) -> None:
        sandbox = await self._sandbox_repository.get_by_user_binding(user_id)
        if sandbox is not None:
            await self._sandbox_repository.finish_session(sandbox.sandbox_id, session_id)
