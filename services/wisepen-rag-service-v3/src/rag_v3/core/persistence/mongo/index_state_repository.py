"""Mongo adapter：维护 revision 的 staged/applied 可见性指针。"""

from collections.abc import Mapping, Sequence

from pymongo.errors import DuplicateKeyError

from rag_v3.application.document.models import ContentRevision, ResourceIndexState
from rag_v3.domain.entities.documents import ResourceIndexStateEntity
from rag_v3.domain.repositories.index_state import (
    ResourceIndexStateRepository,
    StageAction,
)


class RevisionNotStagedError(RuntimeError):
    """请求 apply 的 revision 已被新 staged revision 取代或从未暂存。"""


class RevisionStateChangedError(RuntimeError):
    """读取 active 指针后其已变化，调用方应重新读取状态后决定是否重试。"""


class MongoResourceIndexStateRepository(ResourceIndexStateRepository):
    """以 Mongo 条件更新保证较旧构建任务不能覆盖较新版本。"""

    async def stage_revision(
        self,
        revision: ContentRevision,
        *,
        expected_applied_content_revision: str | None,
    ) -> StageAction:
        collection = ResourceIndexStateEntity.get_pymongo_collection()
        try:
            result = await collection.update_one(
                _stage_filter(revision, expected_applied_content_revision),
                {
                    "$set": {
                        "staged_content_revision": revision.content_revision,
                        "staged_document_version": revision.document_version,
                    },
                    "$setOnInsert": {"resource_id": revision.resource_id},
                },
                upsert=True,
            )
        except DuplicateKeyError:
            # 并发首次 stage 只允许一个插入；另一个请求改为检查当前状态。
            current = await self._get_state(revision.resource_id)
            return _stage_action(
                revision,
                current,
                expected_applied_content_revision,
            )

        if result.matched_count or result.upserted_id is not None:
            return StageAction.STAGED

        return _stage_action(
            revision,
            await self._get_state(revision.resource_id),
            expected_applied_content_revision,
        )

    async def apply_revision(self, revision: ContentRevision) -> None:
        result = await ResourceIndexStateEntity.get_pymongo_collection().update_one(
            {
                "resource_id": revision.resource_id,
                "staged_content_revision": revision.content_revision,
                "staged_document_version": revision.document_version,
            },
            {
                "$set": {"applied_content_revision": revision.content_revision},
                "$unset": {
                    "staged_content_revision": "",
                    "staged_document_version": "",
                },
            },
        )
        if result.modified_count == 1:
            return

        current = await self._get_state(revision.resource_id)
        if current is not None and current.applied_content_revision == revision.content_revision:
            return
        raise RevisionNotStagedError(
            f"content revision {revision.content_revision} is not staged"
        )

    async def get_states(
        self,
        resource_ids: Sequence[str],
    ) -> Mapping[str, ResourceIndexState]:
        unique_resource_ids = list(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return {}
        entities = await ResourceIndexStateEntity.find(
            {"resource_id": {"$in": unique_resource_ids}}
        ).to_list()
        return {entity.resource_id: _to_domain(entity) for entity in entities}

    async def clear_visibility(self, resource_ids: Sequence[str]) -> None:
        unique_resource_ids = list(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return
        # 删除先撤销两个指针；旧 revision 即使尚未物理清理也不再对外可见。
        await ResourceIndexStateEntity.get_pymongo_collection().update_many(
            {"resource_id": {"$in": unique_resource_ids}},
            {
                "$unset": {
                    "staged_content_revision": "",
                    "staged_document_version": "",
                    "applied_content_revision": "",
                }
            },
        )

    @staticmethod
    async def _get_state(resource_id: str) -> ResourceIndexState | None:
        entity = await ResourceIndexStateEntity.find_one({"resource_id": resource_id})
        return None if entity is None else _to_domain(entity)


def _stage_filter(
    revision: ContentRevision,
    expected_applied_content_revision: str | None,
) -> dict[str, object]:
    """条件更新 filter，保证并发 stage/apply 不会覆盖较新 revision。"""
    return {
        "resource_id": revision.resource_id,
        "$and": [
            {
                "$or": [
                    {"staged_document_version": {"$exists": False}},  # 从未暂存
                    {"staged_document_version": None},  # 暂存字段为 null，如被清空
                    # 当前暂存版本 <= 新版本，避免旧数据覆盖新版本
                    {"staged_document_version": {"$lte": revision.document_version}},
                ]
            },
            {
                # 乐观锁校验：当前任务基于预期的 active revision 进行 stage；若 active revision 已被新 revision 覆盖，则拒绝。
                "$or": (
                    [
                        {"applied_content_revision": {"$exists": False}},
                        {"applied_content_revision": None},
                    ]
                    if expected_applied_content_revision is None
                    else [{"applied_content_revision": expected_applied_content_revision}]
                )
            },
        ],
    }


def _stage_action(
    revision: ContentRevision,
    current: ResourceIndexState | None,
    expected_applied_content_revision: str | None,
) -> StageAction:
    """状态判定函数"""
    # 唯一索引并发竞争后记录已被删除时，调用方应重试，而不是猜测发布状态。
    if current is None:
        raise RuntimeError(f"resource {revision.resource_id} stage changed concurrently")
    # 乐观锁冲突，线上版本已被修改
    if current.applied_content_revision != expected_applied_content_revision:
        raise RevisionStateChangedError(
            f"resource {revision.resource_id} active revision changed concurrently"
        )
    # 幂等命中，线上版本与暂存版本一致
    if current.applied_content_revision == revision.content_revision:
        return StageAction.ALREADY_APPLIED
    # 暂存版本已被新版本覆盖，当前 revision 已过期
    if (
        current.staged_document_version is not None
        and current.staged_document_version > revision.document_version
    ):
        return StageAction.STALE
    # 排除了上述所有冲突异常后，状态校验通过，可以继续按正常暂存成功处理
    return StageAction.STAGED


def _to_domain(entity: ResourceIndexStateEntity) -> ResourceIndexState:
    return ResourceIndexState(
        resource_id=entity.resource_id,
        staged_content_revision=entity.staged_content_revision,
        staged_document_version=entity.staged_document_version,
        applied_content_revision=entity.applied_content_revision,
    )
