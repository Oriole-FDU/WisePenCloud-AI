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
    """Mongo-backed 的 Workspace 生命周期与 tombstone 权威存储。"""

    def __init__(self, *, database: Any) -> None:
        self._database = database
        self._workspaces = database["wisepen_sandbox_v1_workspace"]

    async def initialize(self) -> None:
        """校验 Mongo 可用性，并创建 Workspace 生命周期查询所需索引。"""

        # ping 提前暴露连接或权限问题。
        await self._database.command("ping")
        # user_id + session_id 是 Chat 调用侧的稳定业务键。
        await self._workspaces.create_index(
            [("user_id", 1), ("session_id", 1)],
            unique=True,
            name="uniq_user_session",
        )
        # workspace_key 是文件系统路径和 document _id 使用的安全键。
        await self._workspaces.create_index(
            [("workspace_key", 1)],
            unique=True,
            name="uniq_workspace_key",
        )
        # state + last_accessed_at 支撑后续按状态扫描或淘汰排查。
        await self._workspaces.create_index(
            [("state", 1), ("last_accessed_at", 1)],
            name="idx_state_last_accessed_at",
        )
        # tombstone snapshot 索引用于 cache 淘汰后回写不可恢复状态。
        await self._workspaces.create_index(
            [
                ("tombstone_snapshot.workspace_key", 1),
                ("tombstone_snapshot.snapshot_id", 1),
            ],
            name="idx_tombstone_snapshot",
        )

    async def get(self, user_id: str, session_id: str) -> WorkspaceRecord | None:
        """按 user/session 读取 Workspace 权威记录。"""

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
        """确保 Workspace 处于 ACTIVE，除非它已经被逻辑删除。"""

        doc = await self._workspaces.find_one(
            {"user_id": user_id, "session_id": session_id}
        )
        if doc is None:
            # 首次访问创建 ACTIVE 记录，物理目录由 WorkspaceService 负责。
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
            # 逻辑删除后的记录不能被 ensure_active 隐式复活。
            return record

        now = utc_now()
        # 非 DELETED 记录统一刷新为 ACTIVE，并更新访问时间和路径。
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
        """声明开始逻辑删除，并把记录推进到 DELETING。"""

        doc = await self._workspaces.find_one(
            {"user_id": user_id, "session_id": session_id}
        )
        now = utc_now()
        if doc is None:
            # 没有历史记录时也创建 DELETING，便于 finish_delete 落 tombstone。
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
            # 已删除或正在恢复时不重复进入删除流程。
            return record

        # 其他状态进入 DELETING，并增加状态版本。
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
        """完成逻辑删除，写入 tombstone 快照并落到 DELETED。"""

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
        """更新 Workspace 的可恢复快照指针，不改变生命周期状态。"""

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
        """删除流程失败时回滚为 ACTIVE 并记录错误。"""

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
        """尝试开始恢复流程，并返回调用方应执行的下一步。"""

        doc = await self._workspaces.find_one(
            {"user_id": user_id, "session_id": session_id}
        )
        now = utc_now()
        if doc is None:
            # 无历史记录时创建 RESTORING，随后由 cache 恢复为空目录。
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
            # 并发 rebuild 已经在恢复中，调用方应直接返回 workspace_restoring。
            return WorkspaceRestoreStart(
                status=WorkspaceRestoreStartStatus.RESTORING,
                record=record,
            )
        if record.state == WorkspaceState.ACTIVE:
            # 已经 ACTIVE 时无需文件恢复，只刷新访问时间。
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

        # DELETED 等状态通过 CAS 抢占 RESTORING，防止多个请求同时恢复文件。
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
            # CAS 失败通常说明另一个请求已经抢到 RESTORING。
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
        """完成恢复流程，落到 ACTIVE 并记录恢复结果。"""

        now = utc_now()
        # 恢复成功后清理删除/恢复时间，generation 增加代表内容 generation 变化。
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
            # 快照 metadata 可能被 restore/touch 或不可恢复标记更新，需回写最新状态。
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
        """恢复流程失败时回到 DELETED，保留 tombstone 供后续重试。"""

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
        """把引用该 snapshot 的 tombstone 统一标记为不可恢复。"""

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
        """构造新的 WorkspaceRecord，供首次 active/delete/restore 写入使用。"""

        return WorkspaceRecord(
            user_id=user_id,
            session_id=session_id,
            workspace_key=workspace_key,
            workspace_path=workspace_path,
            state=state,
        )
