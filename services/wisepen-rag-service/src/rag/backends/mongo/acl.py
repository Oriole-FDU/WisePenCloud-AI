"""Mongo ACL 投影仓储。"""

from collections.abc import Mapping, Sequence
from typing import Any

from pymongo import ASCENDING, IndexModel
from pymongo.errors import DuplicateKeyError

from rag.application.acl.models import GroupResourceAcl, ResourceAcl
from rag.application.acl.store import ResourceAclRepository


class MongoResourceAclRepository(ResourceAclRepository):
    """保存和读取本地 ACL 投影。"""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def get_resource_acls(
        self,
        resource_ids: Sequence[str],
    ) -> Mapping[str, ResourceAcl]:
        # 去重并保持输入顺序；空输入直接返回，避免无意义查询
        unique_resource_ids = list(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return {}

        records = await self._collection.find(
            {"resource_id": {"$in": unique_resource_ids}}
        ).to_list(length=None)
        return {record["resource_id"]: _to_domain(record) for record in records}

    async def save(self, resource_acl: ResourceAcl) -> bool:
        # 构造更新字段（不含 resource_id，它由 $setOnInsert 负责）
        fields = {
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
                    "market_offer_version": group_acl.market_offer_version,
                }
                for group_acl in resource_acl.group_acls
            ],
        }

        # 仅当新 revision 不小于旧值（或旧值不存在）时才更新，实现乐观并发控制
        revision_filter = {
            "resource_id": resource_acl.resource_id,
            "$or": [
                {"acl_revision": {"$lt": resource_acl.acl_revision}},
                {"acl_revision": {"$exists": False}},
                {"acl_revision": resource_acl.acl_revision},
            ],
        }

        try:
            result = await self._collection.update_one(
                revision_filter,
                {
                    "$set": fields,
                    "$setOnInsert": {"resource_id": resource_acl.resource_id},
                },
                upsert=True,
            )
        except DuplicateKeyError:
            # 并发 upsert 冲突时文档已存在，退化为纯更新
            result = await self._collection.update_one(
                revision_filter, {"$set": fields}
            )
        return bool(result.matched_count or result.upserted_id is not None)

    async def delete(self, resource_id: str) -> None:
        await self._collection.delete_one({"resource_id": resource_id})

    async def ensure_indexes(self) -> None:
        await self._collection.create_indexes(
            [
                IndexModel(
                    [("resource_id", ASCENDING)],
                    unique=True,
                    name="resource_acl_resource_unique",
                )
            ]
        )


def _to_domain(record: Mapping[str, Any]) -> ResourceAcl:
    """还原内部完整投影；类型由权威解析和本仓储 writer 保证，不再逐字段验证。"""
    return ResourceAcl(
        resource_id=record["resource_id"],
        acl_revision=record["acl_revision"],
        owner_id=record["owner_id"],
        readable_users=record["readable_users"],
        excluded_read_users=record["excluded_read_users"],
        group_acls=[
            GroupResourceAcl(
                group_id=group["group_id"],
                default_readable=group["default_readable"],
                readable_users=group["readable_users"],
                excluded_read_users=group["excluded_read_users"],
                # 新字段依赖旧投影重同步补齐，缺项不推断为普通群组权限。
                market_offer_version=group["market_offer_version"],
            )
            for group in record["group_acls"]
        ],
    )
