from __future__ import annotations

from sandbox_v1.core.storage.memory.workspace_state import _WorkspaceRepositoryState
from sandbox_v1.domain.entities import (
    WorkspaceRecord,
    WorkspaceRestoreStart,
    WorkspaceRestoreStartStatus,
    WorkspaceSnapshotRef,
    WorkspaceState,
    utc_now,
)


class MemoryWorkspaceRepository:
    """In-process Workspace authority used until Mongo is wired.

    State changes are intentionally explicit. The service can return
    workspace_restoring without waiting for filesystem work because RESTORING
    is committed before the restore copy starts.
    """

    def __init__(self) -> None:
        self._state = _WorkspaceRepositoryState()

    @staticmethod
    def _key(user_id: str, session_id: str) -> tuple[str, str]:
        return (user_id, session_id)

    @staticmethod
    def _new_record(
        *,
        user_id: str,
        session_id: str,
        workspace_key: str,
        workspace_path: str,
        state: WorkspaceState = WorkspaceState.ACTIVE,
    ) -> WorkspaceRecord:
        return WorkspaceRecord(
            user_id=user_id,
            session_id=session_id,
            workspace_key=workspace_key,
            workspace_path=workspace_path,
            state=state,
        )

    async def get(self, user_id: str, session_id: str) -> WorkspaceRecord | None:
        async with self._state.lock:
            return self._state.records.get(self._key(user_id, session_id))

    async def ensure_active(
        self,
        *,
        user_id: str,
        session_id: str,
        workspace_key: str,
        workspace_path: str,
    ) -> WorkspaceRecord:
        async with self._state.lock:
            key = self._key(user_id, session_id)
            record = self._state.records.get(key)
            now = utc_now()
            if record is None:
                record = self._new_record(
                    user_id=user_id,
                    session_id=session_id,
                    workspace_key=workspace_key,
                    workspace_path=workspace_path,
                )
                self._state.records[key] = record
            if record.state == WorkspaceState.DELETED:
                return record
            record.state = WorkspaceState.ACTIVE
            record.workspace_path = workspace_path
            record.updated_at = now
            record.last_accessed_at = now
            record.last_error = None
            return record

    async def begin_delete(
        self,
        *,
        user_id: str,
        session_id: str,
        workspace_key: str,
        workspace_path: str,
    ) -> WorkspaceRecord:
        async with self._state.lock:
            key = self._key(user_id, session_id)
            record = self._state.records.get(key)
            now = utc_now()
            if record is None:
                record = self._new_record(
                    user_id=user_id,
                    session_id=session_id,
                    workspace_key=workspace_key,
                    workspace_path=workspace_path,
                    state=WorkspaceState.DELETING,
                )
                self._state.records[key] = record
            elif record.state in {WorkspaceState.DELETED, WorkspaceState.RESTORING}:
                return record
            else:
                record.state = WorkspaceState.DELETING
                record.workspace_path = workspace_path

            record.state_version += 1
            record.updated_at = now
            record.last_error = None
            return record

    async def finish_delete(
        self,
        *,
        user_id: str,
        session_id: str,
        snapshot: WorkspaceSnapshotRef | None,
    ) -> WorkspaceRecord:
        async with self._state.lock:
            key = self._key(user_id, session_id)
            record = self._state.records[key]
            now = utc_now()
            record.state = WorkspaceState.DELETED
            record.tombstone_snapshot = snapshot
            record.deleted_at = now
            record.updated_at = now
            record.state_version += 1
            record.last_error = None
            return record

    async def remember_snapshot(
        self,
        *,
        user_id: str,
        session_id: str,
        snapshot: WorkspaceSnapshotRef,
    ) -> WorkspaceRecord:
        async with self._state.lock:
            record = self._state.records[self._key(user_id, session_id)]
            record.tombstone_snapshot = snapshot
            record.updated_at = utc_now()
            record.state_version += 1
            record.last_error = None
            return record

    async def fail_delete(
        self,
        *,
        user_id: str,
        session_id: str,
        error: str,
    ) -> WorkspaceRecord:
        async with self._state.lock:
            record = self._state.records[self._key(user_id, session_id)]
            record.state = WorkspaceState.ACTIVE
            record.updated_at = utc_now()
            record.state_version += 1
            record.last_error = error
            return record

    async def begin_restore(
        self,
        *,
        user_id: str,
        session_id: str,
        workspace_key: str,
        workspace_path: str,
    ) -> WorkspaceRestoreStart:
        async with self._state.lock:
            key = self._key(user_id, session_id)
            record = self._state.records.get(key)
            now = utc_now()
            if record is None:
                record = self._new_record(
                    user_id=user_id,
                    session_id=session_id,
                    workspace_key=workspace_key,
                    workspace_path=workspace_path,
                    state=WorkspaceState.RESTORING,
                )
                self._state.records[key] = record
            elif record.state == WorkspaceState.RESTORING:
                return WorkspaceRestoreStart(
                    status=WorkspaceRestoreStartStatus.RESTORING,
                    record=record,
                )
            elif record.state == WorkspaceState.ACTIVE:
                record.updated_at = now
                record.last_accessed_at = now
                return WorkspaceRestoreStart(
                    status=WorkspaceRestoreStartStatus.ALREADY_ACTIVE,
                    record=record,
                )
            else:
                record.state = WorkspaceState.RESTORING
                record.workspace_path = workspace_path

            record.restore_started_at = now
            record.updated_at = now
            record.state_version += 1
            record.last_error = None
            return WorkspaceRestoreStart(
                status=WorkspaceRestoreStartStatus.STARTED,
                record=record,
            )

    async def finish_restore(
        self,
        *,
        user_id: str,
        session_id: str,
        restored_from_snapshot: bool,
        snapshot: WorkspaceSnapshotRef | None,
        unrecoverable_reason: str | None = None,
    ) -> WorkspaceRecord:
        async with self._state.lock:
            record = self._state.records[self._key(user_id, session_id)]
            now = utc_now()
            if snapshot is not None:
                record.tombstone_snapshot = snapshot
            record.state = WorkspaceState.ACTIVE
            record.generation += 1
            record.updated_at = now
            record.last_accessed_at = now
            record.restored_at = now
            record.restore_started_at = None
            record.state_version += 1
            record.last_error = unrecoverable_reason
            record.deleted_at = None
            return record

    async def fail_restore(
        self,
        *,
        user_id: str,
        session_id: str,
        error: str,
    ) -> WorkspaceRecord:
        async with self._state.lock:
            record = self._state.records[self._key(user_id, session_id)]
            record.state = WorkspaceState.DELETED
            record.restore_started_at = None
            record.updated_at = utc_now()
            record.state_version += 1
            record.last_error = error
            return record

    async def mark_snapshot_unrecoverable(
        self,
        snapshot: WorkspaceSnapshotRef,
        *,
        reason: str,
    ) -> None:
        async with self._state.lock:
            for record in self._state.records.values():
                current = record.tombstone_snapshot
                if current is None or (
                    current.workspace_key,
                    current.snapshot_id,
                ) != (snapshot.workspace_key, snapshot.snapshot_id):
                    continue
                record.tombstone_snapshot = WorkspaceSnapshotRef(
                    workspace_key=current.workspace_key,
                    snapshot_id=current.snapshot_id,
                    created_at=current.created_at,
                    last_accessed_at=current.last_accessed_at,
                    total_bytes=current.total_bytes,
                    file_count=current.file_count,
                    directory_count=current.directory_count,
                    recoverable=False,
                    unrecoverable_reason=reason,
                    unrecoverable_at=utc_now(),
                )
                record.updated_at = utc_now()
                record.state_version += 1
