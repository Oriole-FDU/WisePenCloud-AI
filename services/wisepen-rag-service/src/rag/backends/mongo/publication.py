"""Mongo 发布状态：条件 stage/apply 与永久销毁标记，不保存内容或投影。"""

from pydantic import TypeAdapter
from pymongo import ASCENDING, IndexModel, UpdateOne
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError

from rag.application.publication.models import (
    ContentConflictError,
    PublicationAction,
    PublicationStateChangedError,
    ResourcePublicationState,
    RevisionNotStagedError,
)
from rag.application.publication.store import PublicationStore
from rag.core.schema import ContentRevision

_state_adapter = TypeAdapter(ResourcePublicationState)
_revision_adapter = TypeAdapter(ContentRevision | None)
_resource_ids_adapter = TypeAdapter(list[str])
_BATCH_SIZE = 500  # bulk_write 批大小


class MongoPublicationStore(PublicationStore):
    """显式绑定 resource_publications；条件失败仅分类，不自动重跑写入。"""

    def __init__(self, collection: AsyncCollection) -> None:
        self._collection = collection

    async def get_states(
        self, resource_ids: list[str]
    ) -> list[ResourcePublicationState]:
        """按 resource_id 批量读取；去重保序，缺失项跳过。"""
        unique_ids = list(dict.fromkeys(resource_ids))
        if not unique_ids:
            return []

        records = await self._collection.find(
            {"resource_id": {"$in": unique_ids}}
        ).to_list(length=None)
        by_id = {record["resource_id"]: record for record in records}
        # $in 结果顺序不保证，按输入顺序还原
        return [
            _state_adapter.validate_python(by_id[resource_id])
            for resource_id in unique_ids
            if resource_id in by_id
        ]

    async def stage_revision(self, revision: ContentRevision) -> PublicationAction:
        """写入 staged 指针；版本冲突或已销毁时返回对应分类。"""
        current = await self._get_state(revision.resource_id)
        action = _stage_action(revision, current)
        if action is not PublicationAction.STAGED:
            return action

        if current is None:
            # 首次 stage：靠唯一索引兜底并发插入
            try:
                await self._collection.insert_one(
                    _state_adapter.dump_python(
                        ResourcePublicationState(revision.resource_id, revision),
                        mode="python",
                    )
                )
                return PublicationAction.STAGED
            except DuplicateKeyError:
                pass
        else:
            # 同时固定两个旧指针与销毁标记，避免覆盖观察后已改变的状态
            staged = _revision_adapter.dump_python(
                current.staged_revision, mode="python"
            )
            applied = _revision_adapter.dump_python(
                current.applied_revision, mode="python"
            )
            result = await self._collection.update_one(
                {
                    "resource_id": revision.resource_id,
                    "staged_revision": staged,
                    "applied_revision": applied,
                    "destroyed": current.destroyed,
                },
                {
                    "$set": {
                        "staged_revision": _revision_adapter.dump_python(
                            revision, mode="python"
                        )
                    }
                },
            )
            # 幂等重放可以没有 modified_count，但必须匹配观察到的状态
            if result.matched_count:
                return PublicationAction.STAGED

        # 条件写入未命中：重新读取，把并发变化归类
        action = _stage_action(revision, await self._get_state(revision.resource_id))
        if action is PublicationAction.STAGED:
            raise PublicationStateChangedError(
                f"resource {revision.resource_id} changed during stage"
            )
        return action

    async def apply_revision(self, revision: ContentRevision) -> PublicationAction:
        """把已 staged 的 revision 提升为 applied；未 staged 时抛错。"""
        revision_record = _revision_adapter.dump_python(revision, mode="python")
        result = await self._collection.update_one(
            {
                "resource_id": revision.resource_id,
                "staged_revision": revision_record,
                "destroyed": False,
            },
            {"$set": {"applied_revision": revision_record, "staged_revision": None}},
        )
        if result.matched_count:
            return PublicationAction.APPLIED

        current = await self._get_state(revision.resource_id)
        action = _stage_action(revision, current)
        if action is not PublicationAction.STAGED:
            return action
        if current is not None and current.staged_revision == revision:
            raise PublicationStateChangedError(
                f"resource {revision.resource_id} changed during apply"
            )
        raise RevisionNotStagedError(
            f"revision {revision.content_revision} is not staged"
        )

    async def destroy_resources(self, resource_ids: list[str]) -> None:
        """写入永久销毁标记；不删记录，防止迟到内容被当成首次摄入。"""
        unique_ids = list(dict.fromkeys(resource_ids))
        for start in range(0, len(unique_ids), _BATCH_SIZE):
            operations = [
                UpdateOne(
                    {"resource_id": resource_id},
                    {
                        "$set": {
                            "destroyed": True,
                            "staged_revision": None,
                            "applied_revision": None,
                        },
                        "$setOnInsert": {"resource_id": resource_id},
                    },
                    upsert=True,
                )
                for resource_id in unique_ids[start : start + _BATCH_SIZE]
            ]
            await self._collection.bulk_write(operations, ordered=True)

    async def ensure_indexes(self) -> None:
        """创建 resource_id 唯一索引，保证每资源至多一条状态记录。"""
        await self._collection.create_indexes(
            [
                IndexModel(
                    [("resource_id", ASCENDING)],
                    unique=True,
                    name="resource_publication_resource_unique",
                ),
                IndexModel(
                    [("destroyed", ASCENDING), ("resource_id", ASCENDING)],
                    name="resource_publication_destroyed_resource",
                ),
            ]
        )

    async def list_destroyed_resource_ids(
        self, *, after_resource_id: str | None = None, limit: int = 100
    ) -> list[str]:
        query = {"destroyed": True}
        if after_resource_id is not None:
            query["resource_id"] = {"$gt": after_resource_id}
        records = (
            await self._collection.find(query, {"resource_id": 1, "_id": 0})
            .sort("resource_id", ASCENDING)
            .limit(limit)
            .to_list(length=limit)
        )
        return _resource_ids_adapter.validate_python(
            [record["resource_id"] for record in records]
        )

    async def _get_state(self, resource_id: str) -> ResourcePublicationState | None:
        record = await self._collection.find_one({"resource_id": resource_id})
        return None if record is None else _state_adapter.validate_python(record)


def _stage_action(
    revision: ContentRevision,
    current: ResourcePublicationState | None,
) -> PublicationAction:
    """先淘汰低版本，再解释最高版本冲突；旧版本不进入同版本冲突判断。"""
    if current is None:
        return PublicationAction.STAGED
    if current.destroyed:
        return PublicationAction.DESTROYED

    known_revisions = (current.staged_revision, current.applied_revision)
    for known in known_revisions:
        if known is not None and known.document_version > revision.document_version:
            return PublicationAction.STALE
    for known in known_revisions:
        if (
            known is not None
            and known.document_version == revision.document_version
            and known.content_sha256 != revision.content_sha256
        ):
            raise ContentConflictError(
                f"resource {revision.resource_id} version {revision.document_version} "
                "has conflicting content"
            )
    if current.applied_revision == revision:
        return PublicationAction.ALREADY_APPLIED
    return PublicationAction.STAGED
