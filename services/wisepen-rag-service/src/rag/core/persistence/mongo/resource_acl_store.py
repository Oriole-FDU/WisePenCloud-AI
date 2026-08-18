"""Beanie adapter：维护本地资源 ACL，不读取上游权限集合。"""

from collections.abc import Mapping, Sequence

from pymongo.errors import DuplicateKeyError

from rag.domain.entities import ResourceAclEntity
from rag.domain.models.acl import GroupResourceAcl, ResourceAcl
from rag.domain.repositories.mongo.resource_acl_store import ResourceAclStore


class MongoResourceAclStore(ResourceAclStore):
    """按资源 ID 查询并按 ACL revision 单调写入本地 ACL。"""

    async def get_resource_acl(self, resource_id: str) -> ResourceAcl | None:
        """直接读取单个本地 ACL；资源不存在时返回 None。"""
        entity = await ResourceAclEntity.find_one({"resource_id": resource_id})
        return None if entity is None else _to_domain(entity)

    async def get_resource_acls(
        self,
        resource_ids: Sequence[str],
    ) -> Mapping[str, ResourceAcl]:
        unique_resource_ids = list(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return {}
        entities = await ResourceAclEntity.find(
            {"resource_id": {"$in": unique_resource_ids}}
        ).to_list()
        return {entity.resource_id: _to_domain(entity) for entity in entities}

    async def save_if_newer(self, resource_acl: ResourceAcl) -> bool:
        collection = ResourceAclEntity.get_pymongo_collection()
        try:
            result = await collection.update_one(
                _newer_or_same_revision_filter(resource_acl),
                {
                    "$set": _to_document(resource_acl),
                    "$setOnInsert": {"resource_id": resource_acl.resource_id},
                },
                upsert=True,
            )
        except DuplicateKeyError:
            # 并发 upsert 可能同时判断资源不存在；唯一索引只允许一个插入成功。
            result = await collection.update_one(
                _newer_or_same_revision_filter(resource_acl),
                {"$set": _to_document(resource_acl)},
            )
        return bool(result.matched_count or result.upserted_id is not None)

    async def delete_resources(self, resource_ids: Sequence[str]) -> None:
        unique_resource_ids = list(dict.fromkeys(resource_ids))
        if unique_resource_ids:
            await ResourceAclEntity.find(
                {"resource_id": {"$in": unique_resource_ids}}
            ).delete()


def _to_domain(entity: ResourceAclEntity) -> ResourceAcl:
    return ResourceAcl(
        resource_id=entity.resource_id,
        acl_revision=entity.acl_revision,
        owner_id=entity.owner_id,
        readable_users=list(entity.readable_users),
        excluded_read_users=list(entity.excluded_read_users),
        group_acls=[
            GroupResourceAcl(
                group_id=group_acl.group_id,
                default_readable=group_acl.is_readable,
                readable_users=list(group_acl.readable_users),
                excluded_read_users=list(group_acl.excluded_read_users),
            )
            for group_acl in entity.group_acls
        ],
    )


def _to_document(resource_acl: ResourceAcl) -> dict[str, object]:
    return {
        "resource_id": resource_acl.resource_id,
        "acl_revision": resource_acl.acl_revision,
        "owner_id": resource_acl.owner_id,
        "readable_users": list(resource_acl.readable_users),
        "excluded_read_users": list(resource_acl.excluded_read_users),
        "group_acls": [
            {
                "group_id": group_acl.group_id,
                "is_readable": group_acl.default_readable,
                "readable_users": list(group_acl.readable_users),
                "excluded_read_users": list(group_acl.excluded_read_users),
            }
            for group_acl in resource_acl.group_acls
        ],
    }


def _newer_or_same_revision_filter(resource_acl: ResourceAcl) -> dict[str, object]:
    return {
        "resource_id": resource_acl.resource_id,
        "$or": [
            {"acl_revision": {"$lt": resource_acl.acl_revision}},
            {"acl_revision": {"$exists": False}},
            # 同 revision 重试要返回 True，让 refresher 补偿此前失败的后端同步。
            {"acl_revision": resource_acl.acl_revision},
        ],
    }
