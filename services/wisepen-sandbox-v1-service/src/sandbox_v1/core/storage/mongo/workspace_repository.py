from __future__ import annotations

from typing import Any

from sandbox_v1.core.storage.mongo.documents import (
    workspace_record_from_doc,
    workspace_record_to_doc,
    workspace_snapshot_to_doc,
)
from sandbox_v1.domain.entities import (
    WorkspaceRecord,
    WorkspaceRestoreStart,
    WorkspaceRestoreStartStatus,
    WorkspaceSnapshotRef,
    WorkspaceState,
    utc_now,
)


class MongoWorkspaceRepository:
    """Mongo-backed authority for Workspace lifecycle and tombstones."""

    def __init__(self, *, database: Any) -> None:
        self._database = database
        self._workspaces = database["wisepen_sandbox_v1_workspace"]

    async def initialize(self) -> None:
        await self._database.command("ping")
        await self._workspaces.create_index(
            [("user_id", 1), ("session_id", 1)],
            unique=True,
            name="uniq_user_session",
        )
        await self._workspaces.create_index(
            [("workspace_key", 1)],
            unique=True,
            name="uniq_workspace_key",
        )
        await self._workspaces.create_index(
            [("state", 1), ("last_accessed_at", 1)],
            name="idx_state_last_accessed_at",
        )
        await self._workspaces.create_index(
            [
                ("tombstone_snapshot.workspace_key", 1),
                ("tombstone_snapshot.snapshot_id", 1),
            ],
            name="idx_tombstone_snapshot",
        )

    async def get(self, user_id: str, session_id: str) -> WorkspaceRecord | None:
        doc = await self._workspaces.find_one(
            {"user_id": user_id, "session_id": session_id}
        )
        return workspace_record_from_doc(doc) if doc is not None else None

    async def ensure_active(
        self,
        *,
        user_id: str,
        session_id: str,
        workspace_key: str,
        workspace_path: str,
    ) -> WorkspaceRecord:
        doc = await self._workspaces.find_one(
            {"user_id": user_id, "session_id": session_id}
        )
        if doc is None:
            record = self._new_record(
                user_id=user_id,
                session_id=session_id,
                workspace_key=workspace_key,
                workspace_path=workspace_path,
            )
            await self._workspaces.update_one(
                {"_id": workspace_key},
                {"$setOnInsert": workspace_record_to_doc(record)},
                upsert=True,
            )
            return record

        record = workspace_record_from_doc(doc)
        if record.state == WorkspaceState.DELETED:
            return record

        now = utc_now()
        updated = await self._workspaces.find_one_and_update(
            {"_id": record.workspace_key, "state": {"$ne": WorkspaceState.DELETED.value}},
            {
                "$set": {
                    "state": WorkspaceState.ACTIVE.value,
                    "workspace_path": workspace_path,
                    "updated_at": now,
                    "last_accessed_at": now,
                    "last_error": None,
                },
            },
            return_document=True,
        )
        return workspace_record_from_doc(updated)

    async def begin_delete(
        self,
        *,
        user_id: str,
        session_id: str,
        workspace_key: str,
        workspace_path: str,
    ) -> WorkspaceRecord:
        doc = await self._workspaces.find_one(
            {"user_id": user_id, "session_id": session_id}
        )
        now = utc_now()
        if doc is None:
            record = self._new_record(
                user_id=user_id,
                session_id=session_id,
                workspace_key=workspace_key,
                workspace_path=workspace_path,
                state=WorkspaceState.DELETING,
            )
            record.state_version = 1
            await self._workspaces.update_one(
                {"_id": workspace_key},
                {"$setOnInsert": workspace_record_to_doc(record)},
                upsert=True,
            )
            return record

        record = workspace_record_from_doc(doc)
        if record.state in {WorkspaceState.DELETED, WorkspaceState.RESTORING}:
            return record

        updated = await self._workspaces.find_one_and_update(
            {"_id": record.workspace_key},
            {
                "$set": {
                    "state": WorkspaceState.DELETING.value,
                    "workspace_path": workspace_path,
                    "updated_at": now,
                    "last_error": None,
                },
                "$inc": {"state_version": 1},
            },
            return_document=True,
        )
        return workspace_record_from_doc(updated)

    async def finish_delete(
        self,
        *,
        user_id: str,
        session_id: str,
        snapshot: WorkspaceSnapshotRef | None,
    ) -> WorkspaceRecord:
        now = utc_now()
        updated = await self._workspaces.find_one_and_update(
            {"user_id": user_id, "session_id": session_id},
            {
                "$set": {
                    "state": WorkspaceState.DELETED.value,
                    "tombstone_snapshot": workspace_snapshot_to_doc(snapshot),
                    "deleted_at": now,
                    "updated_at": now,
                    "last_error": None,
                },
                "$inc": {"state_version": 1},
            },
            return_document=True,
        )
        return workspace_record_from_doc(updated)

    async def remember_snapshot(
        self,
        *,
        user_id: str,
        session_id: str,
        snapshot: WorkspaceSnapshotRef,
    ) -> WorkspaceRecord:
        updated = await self._workspaces.find_one_and_update(
            {"user_id": user_id, "session_id": session_id},
            {
                "$set": {
                    "tombstone_snapshot": workspace_snapshot_to_doc(snapshot),
                    "updated_at": utc_now(),
                    "last_error": None,
                },
                "$inc": {"state_version": 1},
            },
            return_document=True,
        )
        return workspace_record_from_doc(updated)

    async def fail_delete(
        self,
        *,
        user_id: str,
        session_id: str,
        error: str,
    ) -> WorkspaceRecord:
        updated = await self._workspaces.find_one_and_update(
            {"user_id": user_id, "session_id": session_id},
            {
                "$set": {
                    "state": WorkspaceState.ACTIVE.value,
                    "updated_at": utc_now(),
                    "last_error": error,
                },
                "$inc": {"state_version": 1},
            },
            return_document=True,
        )
        return workspace_record_from_doc(updated)

    async def begin_restore(
        self,
        *,
        user_id: str,
        session_id: str,
        workspace_key: str,
        workspace_path: str,
    ) -> WorkspaceRestoreStart:
        doc = await self._workspaces.find_one(
            {"user_id": user_id, "session_id": session_id}
        )
        now = utc_now()
        if doc is None:
            record = self._new_record(
                user_id=user_id,
                session_id=session_id,
                workspace_key=workspace_key,
                workspace_path=workspace_path,
                state=WorkspaceState.RESTORING,
            )
            record.restore_started_at = now
            record.state_version = 1
            await self._workspaces.update_one(
                {"_id": workspace_key},
                {"$setOnInsert": workspace_record_to_doc(record)},
                upsert=True,
            )
            return WorkspaceRestoreStart(
                status=WorkspaceRestoreStartStatus.STARTED,
                record=record,
            )

        record = workspace_record_from_doc(doc)
        if record.state == WorkspaceState.RESTORING:
            return WorkspaceRestoreStart(
                status=WorkspaceRestoreStartStatus.RESTORING,
                record=record,
            )
        if record.state == WorkspaceState.ACTIVE:
            updated = await self._workspaces.find_one_and_update(
                {"_id": record.workspace_key},
                {
                    "$set": {
                        "updated_at": now,
                        "last_accessed_at": now,
                    },
                },
                return_document=True,
            )
            return WorkspaceRestoreStart(
                status=WorkspaceRestoreStartStatus.ALREADY_ACTIVE,
                record=workspace_record_from_doc(updated),
            )

        updated = await self._workspaces.find_one_and_update(
            {
                "_id": record.workspace_key,
                "state": {"$ne": WorkspaceState.RESTORING.value},
            },
            {
                "$set": {
                    "state": WorkspaceState.RESTORING.value,
                    "workspace_path": workspace_path,
                    "restore_started_at": now,
                    "updated_at": now,
                    "last_error": None,
                },
                "$inc": {"state_version": 1},
            },
            return_document=True,
        )
        if updated is None:
            current = await self._workspaces.find_one({"_id": record.workspace_key})
            return WorkspaceRestoreStart(
                status=WorkspaceRestoreStartStatus.RESTORING,
                record=workspace_record_from_doc(current),
            )
        return WorkspaceRestoreStart(
            status=WorkspaceRestoreStartStatus.STARTED,
            record=workspace_record_from_doc(updated),
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
        now = utc_now()
        updates: dict[str, Any] = {
            "state": WorkspaceState.ACTIVE.value,
            "updated_at": now,
            "last_accessed_at": now,
            "restored_at": now,
            "restore_started_at": None,
            "deleted_at": None,
            "last_error": unrecoverable_reason,
        }
        if snapshot is not None:
            updates["tombstone_snapshot"] = workspace_snapshot_to_doc(snapshot)

        updated = await self._workspaces.find_one_and_update(
            {"user_id": user_id, "session_id": session_id},
            {
                "$set": updates,
                "$inc": {"generation": 1, "state_version": 1},
            },
            return_document=True,
        )
        return workspace_record_from_doc(updated)

    async def fail_restore(
        self,
        *,
        user_id: str,
        session_id: str,
        error: str,
    ) -> WorkspaceRecord:
        updated = await self._workspaces.find_one_and_update(
            {"user_id": user_id, "session_id": session_id},
            {
                "$set": {
                    "state": WorkspaceState.DELETED.value,
                    "restore_started_at": None,
                    "updated_at": utc_now(),
                    "last_error": error,
                },
                "$inc": {"state_version": 1},
            },
            return_document=True,
        )
        return workspace_record_from_doc(updated)

    async def mark_snapshot_unrecoverable(
        self,
        snapshot: WorkspaceSnapshotRef,
        *,
        reason: str,
    ) -> None:
        await self._workspaces.update_many(
            {
                "tombstone_snapshot.workspace_key": snapshot.workspace_key,
                "tombstone_snapshot.snapshot_id": snapshot.snapshot_id,
            },
            {
                "$set": {
                    "tombstone_snapshot.recoverable": False,
                    "tombstone_snapshot.unrecoverable_reason": reason,
                    "tombstone_snapshot.unrecoverable_at": utc_now(),
                    "updated_at": utc_now(),
                },
                "$inc": {"state_version": 1},
            },
        )

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
