"""上游 `wispen_resource_items` 的只读 ACL 投影。"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo.asynchronous.collection import AsyncCollection

from rag_v3.domain.acl import GroupResourceAcl, ResourceAcl
from rag_v3.domain.repositories.acl import AuthoritativeAclReader


class AuthoritativeAclError(ValueError):
    """上游资源记录缺失 RAG 读取 ACL 必需的字段。"""


class MongoAuthoritativeAclReader(AuthoritativeAclReader):
    """将上游 ACL 的 VIEW 位掩码投影为 RAG 本地授权事实。"""

    def __init__(self, *, collection: AsyncCollection[dict[str, Any]]) -> None:
        self._collection = collection

    async def get_resource_acls(
        self,
        resource_ids: Sequence[str],
    ) -> dict[str, ResourceAcl]:
        unique_resource_ids = list(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return {}

        if any(not ObjectId.is_valid(resource_id) for resource_id in unique_resource_ids):
            raise AuthoritativeAclError("resource_id must be a valid ObjectId")

        records = await self._collection.find(
            {"_id": {"$in": [ObjectId(resource_id) for resource_id in unique_resource_ids]}}
        ).to_list(length=None)

        return {
            resource_id: _project(record, resource_id)
            for record in records
            if (resource_id := str(record["_id"])) in unique_resource_ids
        }


def _project(record: dict[str, Any], resource_id: str) -> ResourceAcl:
    owner_id = record.get("ownerId")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise AuthoritativeAclError("ownerId must be a non-empty string")

    update_time = record.get("updateTime")
    if not isinstance(update_time, datetime):
        raise AuthoritativeAclError("updateTime must be a datetime")
    if update_time.tzinfo is None:
        update_time = update_time.replace(tzinfo=UTC)

    readable_users, excluded_read_users = _read_user_masks(
        record.get("specifiedUsersGrantedActionsMask")
    )
    return ResourceAcl(
        resource_id=resource_id,
        acl_revision=int(update_time.timestamp() * 1000),
        owner_id=owner_id.strip(),
        readable_users=tuple(readable_users),
        excluded_read_users=tuple(excluded_read_users),
        group_acls=tuple(_read_group_acls(record.get("computedGroupAcls"))),
    )


def _read_user_masks(value: Any) -> tuple[list[str], list[str]]:
    if not isinstance(value, dict):
        return [], []
    readable_users: list[str] = []
    excluded_read_users: list[str] = []
    for user_id, mask in value.items():
        if not isinstance(user_id, str) or not user_id.strip():
            continue
        if isinstance(mask, bool) or not isinstance(mask, int):
            continue
        (readable_users if _has_view(mask) else excluded_read_users).append(user_id.strip())
    return readable_users, excluded_read_users


def _read_group_acls(value: Any) -> list[GroupResourceAcl]:
    if not isinstance(value, dict):
        return []
    result: list[GroupResourceAcl] = []
    for group_id, group_value in value.items():
        if not isinstance(group_id, str) or not group_id.strip():
            continue
        if not isinstance(group_value, dict):
            continue
        default_readable = _has_view(group_value.get("baseMask"))
        readable_users, excluded_read_users = _read_user_masks(
            group_value.get("userMasks")
        )
        result.append(
            GroupResourceAcl(
                group_id=group_id.strip(),
                default_readable=default_readable,
                readable_users=() if default_readable else tuple(readable_users),
                excluded_read_users=(
                    tuple(excluded_read_users) if default_readable else ()
                ),
            )
        )
    return result


def _has_view(mask: Any) -> bool:
    return isinstance(mask, int) and not isinstance(mask, bool) and mask & (1 << 1) != 0
