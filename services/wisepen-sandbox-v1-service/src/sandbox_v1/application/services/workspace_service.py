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
        user_id, session_id = self._validate_ids(user_id, session_id)
        workspace_key = self.workspace_key(user_id, session_id)
        workspace_path = self._workspace_path(workspace_key)
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
        await asyncio.to_thread(self._ensure_workspace_dir, workspace_path)
        record = await self._repository.ensure_active(
            user_id=user_id,
            session_id=session_id,
            workspace_key=workspace_key,
            workspace_path=str(workspace_path),
        )
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
        """Persist the current Workspace before container recycle.

        This is a narrow hook for the recycle phase. It updates the recoverable
        snapshot pointer without changing ACTIVE/DELETED lifecycle state.
        """
        user_id, session_id = self._validate_ids(user_id, session_id)
        workspace_key = self.workspace_key(user_id, session_id)
        workspace_path = self._workspace_path(workspace_key)
        await self._repository.ensure_active(
            user_id=user_id,
            session_id=session_id,
            workspace_key=workspace_key,
            workspace_path=str(workspace_path),
        )
        snapshot = await self._cache.snapshot(
            workspace_key=workspace_key,
            user_id=user_id,
            session_id=session_id,
            source_path=workspace_path,
        )
        if snapshot is not None:
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
        user_id, session_id = self._validate_ids(user_id, session_id)
        workspace_key = self.workspace_key(user_id, session_id)
        workspace_path = self._workspace_path(workspace_key)
        record = await self._repository.begin_delete(
            user_id=user_id,
            session_id=session_id,
            workspace_key=workspace_key,
            workspace_path=str(workspace_path),
        )
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
            snapshot = await self._cache.snapshot(
                workspace_key=workspace_key,
                user_id=user_id,
                session_id=session_id,
                source_path=workspace_path,
            )
            await asyncio.to_thread(self._delete_workspace_dir, workspace_path)
        except ServiceException as exc:
            await self._repository.fail_delete(
                user_id=user_id,
                session_id=session_id,
                error=exc.msg,
            )
            self._metrics.increment("workspace_snapshot_rejections")
            raise
        except Exception as exc:
            await self._repository.fail_delete(
                user_id=user_id,
                session_id=session_id,
                error=str(exc),
            )
            raise

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
        user_id, session_id = self._validate_ids(user_id, session_id)
        workspace_key = self.workspace_key(user_id, session_id)
        workspace_path = self._workspace_path(workspace_key)
        start = await self._repository.begin_restore(
            user_id=user_id,
            session_id=session_id,
            workspace_key=workspace_key,
            workspace_path=str(workspace_path),
        )

        if start.status == WorkspaceRestoreStartStatus.RESTORING:
            return self._result(
                user_id,
                session_id,
                WorkspaceLifecycleStatus.WORKSPACE_RESTORING,
                start.record.workspace_path,
                start.record.tombstone_snapshot,
            )
        if start.status == WorkspaceRestoreStartStatus.ALREADY_ACTIVE:
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
            outcome = await self._cache.restore(
                snapshot,
                target_path=workspace_path,
            )
        except Exception as exc:
            await self._repository.fail_restore(
                user_id=user_id,
                session_id=session_id,
                error=str(exc),
            )
            raise

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
        evicted: list[WorkspaceSnapshotRef] = []
        for snapshot in await self._cache.evict_expired():
            evicted.append(snapshot)
            await self._repository.mark_snapshot_unrecoverable(
                snapshot,
                reason=snapshot.unrecoverable_reason
                or WorkspaceEvictionReason.TTL.value,
            )
            self._metrics.increment("workspace_cache_evictions_ttl")

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
        raw = f"{user_id}\0{session_id}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _workspace_path(self, workspace_key: str) -> Path:
        path = self._workspace_root / workspace_key
        self._assert_under_workspace_root(path)
        return path

    def _assert_under_workspace_root(self, path: Path) -> None:
        root = self._workspace_root.resolve(strict=False)
        target = path.resolve(strict=False)
        if target != root and root not in target.parents:
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_UNSAFE,
                "workspace path is outside the managed root",
            )

    def _ensure_workspace_dir(self, path: Path) -> None:
        self._assert_under_workspace_root(path)
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_UNSAFE,
                "workspace path exists but is not a directory",
            )
        path.mkdir(parents=True, exist_ok=True)

    def _delete_workspace_dir(self, path: Path) -> None:
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
