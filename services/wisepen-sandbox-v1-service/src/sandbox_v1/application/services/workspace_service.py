from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path

from common.core.exceptions import ServiceException

from sandbox_v1.domain.entities import (
    WorkspaceEvictionReason,
    WorkspaceLifecycleResult,
    WorkspaceLifecycleStatus,
    WorkspaceRestoreStartStatus,
    WorkspaceSnapshotRef,
    WorkspaceState,
)
from sandbox_v1.domain.error_codes import SandboxErrorCode
from sandbox_v1.domain.interfaces.metrics import MetricsPort
from sandbox_v1.domain.interfaces.workspace_cache import WorkspaceCache
from sandbox_v1.domain.repositories import WorkspaceRepository


class WorkspaceService:
    """Chat-facing Workspace lifecycle core.

    The service deliberately does not call File/Process/Browser adapters. Stage
    3 owns host snapshot state and logical delete/rebuild behavior; container
    import/export is wired through capability adapters in a later phase.

    Repository 负责生命周期状态和 tombstone 快照指针，WorkspaceCache 负责实际
    文件树快照与恢复。本服务只编排两者，不直接实现持久化或文件复制细节。
    """

    def __init__(
        self,
        *,
        repository: WorkspaceRepository,
        cache: WorkspaceCache,
        workspace_root: str | Path,
        metrics: MetricsPort,
    ) -> None:
        self._repository = repository
        self._cache = cache
        self._workspace_root = Path(workspace_root).resolve(strict=False)
        self._metrics = metrics

    async def ensure_active(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> WorkspaceLifecycleResult:
        """确保指定用户会话的 Workspace 目录存在并返回生命周期结果。

        已删除 Workspace 不会被隐式重新激活；正在恢复的 Workspace 也不会被
        并发请求抢先使用。只有可用记录才会创建/确认物理目录并交给 Repository
        标记为 ACTIVE。
        """

        # 统一清洗并校验用户和会话标识，再生成稳定的 Workspace key/path。
        user_id, session_id = self._validate_ids(user_id, session_id)
        workspace_key = self.workspace_key(user_id, session_id)
        workspace_path = self._workspace_path(workspace_key)

        # 先读取权威状态，避免 DELETED/RESTORING 被普通 ensure 请求覆盖。
        existing = await self._repository.get(user_id, session_id)
        if existing is not None and existing.state == WorkspaceState.DELETED:
            return self._result(
                user_id,
                session_id,
                WorkspaceLifecycleStatus.WORKSPACE_DELETED,
                existing.workspace_path,
                existing.tombstone_snapshot,
            )
        if existing is not None and existing.state == WorkspaceState.RESTORING:
            return self._result(
                user_id,
                session_id,
                WorkspaceLifecycleStatus.WORKSPACE_RESTORING,
                existing.workspace_path,
                existing.tombstone_snapshot,
            )

        # 物理目录由本服务确认存在，再同步 Repository 的 ACTIVE 状态。
        await asyncio.to_thread(self._ensure_workspace_dir, workspace_path)
        record = await self._repository.ensure_active(
            user_id=user_id,
            session_id=session_id,
            workspace_key=workspace_key,
            workspace_path=str(workspace_path),
        )

        # Repository 仍可能在并发请求间返回终态，统一转换为对外结果。
        if record.state == WorkspaceState.DELETED:
            return self._result(
                user_id,
                session_id,
                WorkspaceLifecycleStatus.WORKSPACE_DELETED,
                record.workspace_path,
                record.tombstone_snapshot,
            )
        return self._result(
            user_id,
            session_id,
            WorkspaceLifecycleStatus.WORKSPACE_READY,
            str(workspace_path),
            record.tombstone_snapshot,
        )

    async def save_before_recycle(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> WorkspaceSnapshotRef | None:
        """在容器回收前保存当前 Workspace 的可恢复快照。

        这是回收阶段使用的窄接口：只更新可恢复快照指针，不主动改变 ACTIVE/
        DELETED 生命周期状态。
        """

        # 先校验标识并确保 Repository 有对应 Workspace 记录。
        user_id, session_id = self._validate_ids(user_id, session_id)
        workspace_key = self.workspace_key(user_id, session_id)
        workspace_path = self._workspace_path(workspace_key)
        await self._repository.ensure_active(
            user_id=user_id,
            session_id=session_id,
            workspace_key=workspace_key,
            workspace_path=str(workspace_path),
        )

        # Cache 负责复制目录树和生成 snapshot metadata。
        snapshot = await self._cache.snapshot(
            workspace_key=workspace_key,
            user_id=user_id,
            session_id=session_id,
            source_path=workspace_path,
        )
        if snapshot is not None:
            # Repository 只保存快照指针，供后续 rebuild 精确恢复。
            await self._repository.remember_snapshot(
                user_id=user_id,
                session_id=session_id,
                snapshot=snapshot,
            )
            self._metrics.increment("workspace_snapshots_created")
        return snapshot

    async def logical_delete(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> WorkspaceLifecycleResult:
        """对 Workspace 执行逻辑删除，并在删除物理目录前保存快照。

        Repository 先进入 DELETING，快照和物理目录删除成功后再落到 DELETED；
        任一步失败都会回滚为 ACTIVE 并保留错误原因。
        """

        # 计算稳定 key/path，并让 Repository 先声明进入删除流程。
        user_id, session_id = self._validate_ids(user_id, session_id)
        workspace_key = self.workspace_key(user_id, session_id)
        workspace_path = self._workspace_path(workspace_key)
        record = await self._repository.begin_delete(
            user_id=user_id,
            session_id=session_id,
            workspace_key=workspace_key,
            workspace_path=str(workspace_path),
        )

        # 已完成删除或正在恢复时，直接返回当前权威状态，不重复操作文件。
        if record.state == WorkspaceState.DELETED:
            return self._result(
                user_id,
                session_id,
                WorkspaceLifecycleStatus.WORKSPACE_DELETED,
                record.workspace_path,
                record.tombstone_snapshot,
            )
        if record.state == WorkspaceState.RESTORING:
            return self._result(
                user_id,
                session_id,
                WorkspaceLifecycleStatus.WORKSPACE_RESTORING,
                record.workspace_path,
                record.tombstone_snapshot,
            )

        try:
            # 先创建 tombstone 快照，再删除物理 Workspace 目录。
            snapshot = await self._cache.snapshot(
                workspace_key=workspace_key,
                user_id=user_id,
                session_id=session_id,
                source_path=workspace_path,
            )
            await asyncio.to_thread(self._delete_workspace_dir, workspace_path)
        except ServiceException as exc:
            # 快照拒绝或路径错误时回滚 ACTIVE，并保留可观测错误。
            await self._repository.fail_delete(
                user_id=user_id,
                session_id=session_id,
                error=exc.msg,
            )
            self._metrics.increment("workspace_snapshot_rejections")
            raise
        except Exception as exc:
            # 其他快照/文件系统异常同样回滚 ACTIVE。
            await self._repository.fail_delete(
                user_id=user_id,
                session_id=session_id,
                error=str(exc),
            )
            raise

        # 文件操作成功后，提交 DELETED 和 tombstone 快照指针。
        record = await self._repository.finish_delete(
            user_id=user_id,
            session_id=session_id,
            snapshot=snapshot,
        )
        self._metrics.increment("workspace_logical_deletes")
        return self._result(
            user_id,
            session_id,
            WorkspaceLifecycleStatus.WORKSPACE_DELETED,
            record.workspace_path,
            record.tombstone_snapshot,
        )

    async def rebuild(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> WorkspaceLifecycleResult:
        """从 tombstone 快照重建 Workspace，或在快照不可用时创建空目录。

        Repository 通过 begin_restore 串行化并发 rebuild：已经 RESTORING 的请求
        立即返回 workspace_restoring，已经 ACTIVE 的请求只确认目录存在。
        """

        # 先校验标识并请求 Repository 抢占恢复流程。
        user_id, session_id = self._validate_ids(user_id, session_id)
        workspace_key = self.workspace_key(user_id, session_id)
        workspace_path = self._workspace_path(workspace_key)
        start = await self._repository.begin_restore(
            user_id=user_id,
            session_id=session_id,
            workspace_key=workspace_key,
            workspace_path=str(workspace_path),
        )

        # 并发恢复只返回状态，不重复执行文件恢复。
        if start.status == WorkspaceRestoreStartStatus.RESTORING:
            return self._result(
                user_id,
                session_id,
                WorkspaceLifecycleStatus.WORKSPACE_RESTORING,
                start.record.workspace_path,
                start.record.tombstone_snapshot,
            )
        if start.status == WorkspaceRestoreStartStatus.ALREADY_ACTIVE:
            # 已激活 Workspace 不重新覆盖文件，只确保物理目录仍然存在。
            await asyncio.to_thread(self._ensure_workspace_dir, workspace_path)
            return self._result(
                user_id,
                session_id,
                WorkspaceLifecycleStatus.WORKSPACE_READY,
                str(workspace_path),
                start.record.tombstone_snapshot,
            )

        snapshot = start.record.tombstone_snapshot
        try:
            # Cache 根据 tombstone 的精确 snapshot_id 恢复；无快照时创建空目录。
            outcome = await self._cache.restore(
                snapshot,
                target_path=workspace_path,
            )
        except Exception as exc:
            # 恢复失败时回滚为 DELETED，下一次请求仍可重试。
            await self._repository.fail_restore(
                user_id=user_id,
                session_id=session_id,
                error=str(exc),
            )
            raise

        # 文件恢复完成后提交 ACTIVE，并记录恢复来源和不可恢复原因。
        record = await self._repository.finish_restore(
            user_id=user_id,
            session_id=session_id,
            restored_from_snapshot=outcome.restored_from_snapshot,
            snapshot=snapshot,
            unrecoverable_reason=outcome.unrecoverable_reason,
        )
        self._metrics.increment(
            "workspace_restores_from_snapshot"
            if outcome.restored_from_snapshot else "workspace_restores_empty"
        )
        return self._result(
            user_id,
            session_id,
            WorkspaceLifecycleStatus.WORKSPACE_READY,
            record.workspace_path,
            record.tombstone_snapshot,
            restored_from_snapshot=outcome.restored_from_snapshot,
            unrecoverable_reason=outcome.unrecoverable_reason,
        )

    async def evict_snapshots(self) -> list[WorkspaceSnapshotRef]:
        """淘汰过期和超容量的快照，并回写不可恢复原因。"""

        evicted: list[WorkspaceSnapshotRef] = []

        # TTL 淘汰优先处理长期未访问的快照。
        for snapshot in await self._cache.evict_expired():
            evicted.append(snapshot)
            await self._repository.mark_snapshot_unrecoverable(
                snapshot,
                reason=snapshot.unrecoverable_reason
                or WorkspaceEvictionReason.TTL.value,
            )
            self._metrics.increment("workspace_cache_evictions_ttl")

        # LRU 淘汰再处理超过缓存水位的最旧可恢复快照。
        for snapshot in await self._cache.evict_lru():
            evicted.append(snapshot)
            await self._repository.mark_snapshot_unrecoverable(
                snapshot,
                reason=snapshot.unrecoverable_reason
                or WorkspaceEvictionReason.LRU.value,
            )
            self._metrics.increment("workspace_cache_evictions_lru")
        return evicted

    @staticmethod
    def workspace_key(user_id: str, session_id: str) -> str:
        """用用户和会话标识生成不可逆且文件系统安全的稳定 key。"""

        raw = f"{user_id}\0{session_id}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _workspace_path(self, workspace_key: str) -> Path:
        """根据 workspace_key 构造并校验受管根目录下的物理路径。"""

        path = self._workspace_root / workspace_key
        self._assert_under_workspace_root(path)
        return path

    def _assert_under_workspace_root(self, path: Path) -> None:
        """确保路径解析后仍位于 Workspace 受管根目录内。"""

        root = self._workspace_root.resolve(strict=False)
        target = path.resolve(strict=False)
        if target != root and root not in target.parents:
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_UNSAFE,
                "workspace path is outside the managed root",
            )

    def _ensure_workspace_dir(self, path: Path) -> None:
        """校验 Workspace 路径类型并创建缺失目录。"""

        self._assert_under_workspace_root(path)
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_UNSAFE,
                "workspace path exists but is not a directory",
            )
        path.mkdir(parents=True, exist_ok=True)

    def _delete_workspace_dir(self, path: Path) -> None:
        """校验 Workspace 路径后删除物理目录；目录不存在时视为已完成。"""

        self._assert_under_workspace_root(path)
        if not path.exists() and not path.is_symlink():
            return
        if path.is_symlink() or not path.is_dir():
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_UNSAFE,
                "workspace path exists but is not a managed directory",
            )
        shutil.rmtree(path)

    @staticmethod
    def _validate_ids(user_id: str, session_id: str) -> tuple[str, str]:
        """清洗并校验 Workspace 生命周期接口使用的标识。"""

        user_id = (user_id or "").strip()
        session_id = (session_id or "").strip()
        if not user_id or not session_id:
            raise ServiceException(
                SandboxErrorCode.INVALID_WORKSPACE_REQUEST,
                "user_id and session_id are required",
            )
        return user_id, session_id

    @staticmethod
    def _result(
        user_id: str,
        session_id: str,
        status: WorkspaceLifecycleStatus,
        workspace_path: str | None,
        snapshot: WorkspaceSnapshotRef | None,
        *,
        restored_from_snapshot: bool = False,
        unrecoverable_reason: str | None = None,
    ) -> WorkspaceLifecycleResult:
        """把 Repository 记录和 cache 结果组装成对外生命周期结果。"""

        return WorkspaceLifecycleResult(
            user_id=user_id,
            session_id=session_id,
            status=status,
            workspace_path=workspace_path,
            snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
            restored_from_snapshot=restored_from_snapshot,
            unrecoverable_reason=unrecoverable_reason
            or (snapshot.unrecoverable_reason if snapshot is not None else None),
        )
