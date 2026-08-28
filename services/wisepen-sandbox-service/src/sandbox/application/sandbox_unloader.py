from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from common.logger import error
from common.core.exceptions import ServiceException

from sandbox.application.container_manager import ContainerManager
from sandbox.core.config.app_settings import settings
from sandbox.domain.entities import (
    SandboxDocument,
    SandboxState,
    SessionWorkspaceDocument,
    WorkspaceExportBundleRef,
    WorkspaceState,
)
from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.domain.repositories import SandboxRepository, WorkspaceRepository


class SandboxUnloader:
    """先快照整个用户沙箱，再销毁其容器。"""

    def __init__(
        self,
        sandbox_repository: SandboxRepository,
        workspace_repository: WorkspaceRepository,
        container_manager: ContainerManager,
    ) -> None:
        self._sandbox_repository = sandbox_repository
        self._workspace_repository = workspace_repository
        self._container_manager = container_manager

    async def unload(self, sandbox: SandboxDocument) -> bool:
        if sandbox.state != SandboxState.RETIRING or not sandbox.bind_user_id:
            return False
        workspaces = await self._workspace_repository.list_attached_by_sandbox(sandbox.sandbox_id)
        try:
            # 存储当前沙箱快照
            snapshot = await self._snapshot(sandbox, workspaces)
            # detach 并记录 workspace 的导出包
            for workspace in workspaces:
                bundle = WorkspaceExportBundleRef(id=f"{sandbox.sandbox_id}-{workspace.id}", workspace_id=workspace.id, bundle_path=str(snapshot / workspace.id))
                if await self._workspace_repository.change_state(workspace.id, WorkspaceState.DETACHED, export_bundle=bundle, clear_runtime_binding=True) is None:
                    return False
            # 摧毁沙箱
            destroying = await self._sandbox_repository.change_state(sandbox.sandbox_id, SandboxState.DESTROYING, clear_user_binding=True)
            if destroying is None:
                return False
            await self._container_manager.destroy(destroying.container_id)
            await self._sandbox_repository.change_state(sandbox.sandbox_id, SandboxState.DESTROYED)
            return True
        except Exception as exc:
            error("sandbox unload failed", exc=exc, sandbox_id=sandbox.sandbox_id)
            return False

    async def _snapshot(
        self,
        sandbox: SandboxDocument,
        workspaces: list[SessionWorkspaceDocument],
    ) -> Path:
        user_id = sandbox.bind_user_id or ""
        sandbox_id = sandbox.sandbox_id
        root = Path(settings.SANDBOX_WORKSPACE_CACHE_ROOT) / user_id
        final = root / sandbox_id
        if final.is_dir() and all((final / workspace.id).is_dir() for workspace in workspaces):
            return final

        # 每次导出都先写入独立的 staging 目录，确认所有 workspace 都已导出后
        # 再替换旧快照。这样失败的导出不会留下可被误用的不完整快照。
        last_error: Exception | None = None
        for _ in range(settings.SANDBOX_WORKSPACE_CACHE_RETRY_COUNT):
            staging = root / f".{sandbox_id}.staging-{uuid4().hex}"
            try:
                await self._container_manager.export_sandbox_workspaces(sandbox.container_id, staging)
                if not all((staging / workspace.id).is_dir() for workspace in workspaces):
                    raise ServiceException(SandboxErrorCode.SNAPSHOT_FAILED, "workspace snapshot is incomplete")
                root.mkdir(parents=True, exist_ok=True)
                if final.exists():
                    shutil.rmtree(final)
                staging.rename(final)
                return final
            except Exception as exc:
                last_error = exc
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
        raise ServiceException(SandboxErrorCode.SNAPSHOT_FAILED,f"sandbox snapshot failed after {settings.SANDBOX_WORKSPACE_CACHE_RETRY_COUNT} attempts: {last_error}") from last_error
