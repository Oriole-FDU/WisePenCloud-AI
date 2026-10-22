from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from common.core.exceptions import ServiceException
from common.logger import error
from pymongo.errors import DuplicateKeyError

from sandbox.application.container_manager import ContainerManager
from sandbox.core.config.app_settings import settings
from sandbox.domain.entities import SandboxDocument, SandboxState, SessionWorkspaceDocument
from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.domain.repositories import SandboxRepository, WorkspaceRepository


class WorkspaceManager:
    """管理 session 对应的 workspace """

    def __init__(
        self,
        sandbox_repository: SandboxRepository,
        workspace_repository: WorkspaceRepository,
        container_manager: ContainerManager,
    ) -> None:
        self._sandbox_repository = sandbox_repository
        self._workspace_repository = workspace_repository
        self._container_manager = container_manager

    async def allocate(self, user_id: str, session_id: str) -> SessionWorkspaceDocument:
        # 获取工作区记录和用户沙箱
        sandbox = await self._sandbox_repository.get_by_user_binding(user_id)
        workspace: SessionWorkspaceDocument | None = await self._workspace_repository.get_by_user_session(user_id, session_id)

        if sandbox is None:
            try:
                # 从 READY 池取得预热容器并绑定给用户
                sandbox = await self._sandbox_repository.assign_to_user(user_id)
            except DuplicateKeyError:
                # 用户绑定唯一键冲突时，读取已有绑定
                sandbox = await self._sandbox_repository.get_by_user_binding(user_id)

        # 查询也可能返回 RETIRING 沙箱，须等其导出结束后才能重新分配
        if sandbox.state != SandboxState.USER_ACTIVE:
            raise ServiceException(SandboxErrorCode.SANDBOX_BUSY)

        # 目录已在当前沙箱，直接返回 workspace 对象
        if workspace and workspace.sandbox_id == sandbox.sandbox_id and workspace.workspace_path:
            return workspace

        # workspace 首次创建时生成 ID
        workspace_id = workspace.id if workspace is not None else uuid4().hex

        # 查询 session_id 对应的 workspace 缓存
        cache = Path(settings.SANDBOX_WORKSPACE_EXPORT_ROOT) / workspace_id

        # None 表示尚未获得可用的容器目录，后面据此决定是否创建空目录。
        workspace_path = None
        try:
            if cache.is_dir():
                # 复制该 session 的 workspace 缓存到容器，以 workspace ID 作为目录名
                workspace_path = await self._container_manager.copy_workspace_from_cache_to_container(
                    sandbox.container_id, workspace_id, str(cache),
                )
        except Exception as exc:
            # 缓存允许丢失，记录恢复错误后尝试使用空目录
            error("session workspace restore failed; starting empty", exc=exc, session_id=session_id)

        # 缓存缺失或恢复失败时建立新的目录
        if workspace_path is None:
            workspace_path = await self._container_manager.create_workspace_directory(
                sandbox.container_id, workspace_id,
            )

        # session 的 workspace 已经存在时只更新原记录的运行位置，首次创建新增记录
        if workspace is not None:
            await self._workspace_repository.set_attached_workspace(
                workspace_id, sandbox.sandbox_id, workspace_path,
            )
            workspace.workspace_path = workspace_path
            return workspace
        else:
            workspace = SessionWorkspaceDocument(
                id=workspace_id,
                user_id=user_id,
                session_id=session_id,
                sandbox_id=sandbox.sandbox_id,
                workspace_path=workspace_path,
            )
            await self._workspace_repository.save(workspace)
            # 返回 workspace
            return workspace

    async def reclaim(self, sandbox: SandboxDocument) -> None:
        # Watcher 已将沙箱标记为 RETIRING，此处接手文件导出
        if sandbox.state != SandboxState.RETIRING or not sandbox.bind_user_id:
            raise ValueError("only a RETIRING user sandbox can be reclaimed")

        # 列出当前沙箱里的 workspace
        workspaces = await self._workspace_repository.list_by_sandbox(sandbox.sandbox_id)

        for workspace in workspaces:
            try:
                # 每个 session 的 workspace 各自复制一次、各自替换缓存；失败由本轮循环记录并跳过
                await self._export_workspace(sandbox, workspace)
            except Exception as exc:
                error(
                    "session export failed; continuing reclaim", exc=exc,
                    sandbox_id=sandbox.sandbox_id, session_id=workspace.session_id,
                )

        # 导出阶段结束，解除用户绑定，状态推进至 DESTROYING
        await self._sandbox_repository.change_state(
            sandbox.sandbox_id, SandboxState.DESTROYING, clear_user_binding=True,
        )
        # 销毁容器
        await self._container_manager.destroy(sandbox.container_id)

    async def _export_workspace(
        self,
        sandbox: SandboxDocument,
        workspace: SessionWorkspaceDocument,
    ) -> None:
        final_cache = Path(settings.SANDBOX_WORKSPACE_EXPORT_ROOT) / workspace.id

        # 临时目录与正式目录同级，复制完成后在同一文件系统内改名发布
        staging_cache = final_cache.parent / f".{final_cache.name}.staging-{uuid4().hex}"

        try:
            # 复制 workspace 的内容至临时目录
            await self._container_manager.copy_workspace_from_container_to_cache(
                sandbox.container_id, workspace.workspace_path, staging_cache,
            )
            # 成功后完整替换
            if final_cache.exists():
                shutil.rmtree(final_cache)
            # 删除再改名不是原子事务，业务允许在此处中断时损失这份缓存
            staging_cache.rename(final_cache)
        finally:
            # 失败清理半成品；成功改名后临时目录已不存在，清理错误不覆盖原结果
            shutil.rmtree(staging_cache, ignore_errors=True)
