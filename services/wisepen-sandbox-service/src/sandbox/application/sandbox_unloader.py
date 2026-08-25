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
            snapshot = await self._snapshot(sandbox, workspaces)
            for workspace in workspaces:
                bundle = WorkspaceExportBundleRef(
                    id=f"{sandbox.sandbox_id}-{workspace.id}",
                    workspace_id=workspace.id,
                    bundle_path=str(snapshot / workspace.id),
                )
                changed = await self._workspace_repository.change_state(
                    workspace.id,
                    WorkspaceState.DETACHED,
                    expected_state=WorkspaceState.ATTACHED,
                    export_bundle=bundle,
                    clear_runtime_binding=True,
                )
                if changed is None:
                    return False

            destroying = await self._sandbox_repository.change_state(
                sandbox.sandbox_id,
                SandboxState.DESTROYING,
                expected_state=SandboxState.RETIRING,
            )
            if destroying is None:
                return False
            await self._container_manager.destroy(destroying.container_id)
            await self._sandbox_repository.change_state(
                sandbox.sandbox_id,
                SandboxState.DESTROYED,
                expected_state=SandboxState.DESTROYING,
                clear_user_binding=True,
            )
            return True
        except Exception as exc:
            error("sandbox unload failed", exc=exc, sandbox_id=sandbox.sandbox_id)
            return False

    async def snapshot_with_retries(self, sandbox: SandboxDocument) -> Path:
        workspaces = await self._workspace_repository.list_attached_by_sandbox(sandbox.sandbox_id)
        return await self._snapshot(sandbox, workspaces)

    async def _snapshot(
        self,
        sandbox: SandboxDocument,
        workspaces: list[SessionWorkspaceDocument],
    ) -> Path:
        user_id = self._safe_component(sandbox.bind_user_id or "")
        sandbox_id = self._safe_component(sandbox.sandbox_id)
        root = Path(settings.SANDBOX_WORKSPACE_CACHE_ROOT) / user_id
        final = root / sandbox_id
        if final.is_dir() and all((final / workspace.id).is_dir() for workspace in workspaces):
            return final

        last_error: Exception | None = None
        for _ in range(settings.SANDBOX_WORKSPACE_CACHE_RETRY_COUNT):
            staging = root / f".{sandbox_id}.staging-{uuid4().hex}"
            backup = root / f".{sandbox_id}.backup-{uuid4().hex}"
            try:
                await self._container_manager.export_sandbox_workspaces(sandbox.container_id, staging)
                root.mkdir(parents=True, exist_ok=True)
                if final.exists():
                    final.rename(backup)
                staging.rename(final)
                if not all((final / workspace.id).is_dir() for workspace in workspaces):
                    raise ServiceException(SandboxErrorCode.SNAPSHOT_FAILED, "workspace snapshot is incomplete")
                if backup.exists():
                    shutil.rmtree(backup)
                return final
            except Exception as exc:
                last_error = exc
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
                if backup.exists():
                    if final.exists():
                        shutil.rmtree(final, ignore_errors=True)
                    backup.rename(final)
        raise ServiceException(
            SandboxErrorCode.SNAPSHOT_FAILED,
            f"sandbox snapshot failed after {settings.SANDBOX_WORKSPACE_CACHE_RETRY_COUNT} attempts: {last_error}",
        ) from last_error

    @staticmethod
    def _safe_component(value: str) -> str:
        if not value or value in {".", ".."} or Path(value).name != value:
            raise ServiceException(SandboxErrorCode.SNAPSHOT_FAILED, "invalid snapshot path component")
        return value
