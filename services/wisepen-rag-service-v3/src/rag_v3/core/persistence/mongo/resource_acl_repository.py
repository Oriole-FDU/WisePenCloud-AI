"""Mongo adapter：保存 RAG 本地 ACL 副本。"""

from collections.abc import Mapping, Sequence

from pymongo.errors import DuplicateKeyError

from rag_v3.domain.acl import GroupResourceAcl, ResourceAcl
from rag_v3.domain.entities.resource_acl import ResourceAclEntity
from rag_v3.domain.repositories.acl import ResourceAclRepository


class MongoResourceAclRepository(ResourceAclRepository):
    """按 ACL revision 单调保存、按资源批量读取本地权限事实。"""

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
        fields = _to_document(resource_acl)
        fields.pop("resource_id")
        try:
            result = await collection.update_one(
                _newer_or_same_revision_filter(resource_acl),
                {
                    "$set": fields,
                    "$setOnInsert": {"resource_id": resource_acl.resource_id},
                },
                upsert=True,
            )
        except DuplicateKeyError:
            # 与旧实现一致：并发首次写入后，同 revision 重试仍视为成功。
            result = await collection.update_one(
                _newer_or_same_revision_filter(resource_acl),
                {"$set": fields},
            )
        return bool(result.matched_count or result.upserted_id is not None)


def _newer_or_same_revision_filter(resource_acl: ResourceAcl) -> dict[str, object]:
    return {
        "resource_id": resource_acl.resource_id,
        "$or": [
            {"acl_revision": {"$lt": resource_acl.acl_revision}},
            {"acl_revision": {"$exists": False}},
            {"acl_revision": resource_acl.acl_revision},
        ],
    }


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
                "default_readable": group_acl.default_readable,
                "readable_users": list(group_acl.readable_users),
                "excluded_read_users": list(group_acl.excluded_read_users),
            }
            for group_acl in resource_acl.group_acls
        ],
    }


def _to_domain(entity: ResourceAclEntity) -> ResourceAcl:
    return ResourceAcl(
        resource_id=entity.resource_id,
        acl_revision=entity.acl_revision,
        owner_id=entity.owner_id,
        readable_users=tuple(entity.readable_users),
        excluded_read_users=tuple(entity.excluded_read_users),
        group_acls=tuple(
            GroupResourceAcl(
                group_id=group_acl.group_id,
                default_readable=group_acl.default_readable,
                readable_users=tuple(group_acl.readable_users),
                excluded_read_users=tuple(group_acl.excluded_read_users),
            )
            for group_acl in entity.group_acls
        ),
    )
