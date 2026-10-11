"""ACL 事件、权威事实读取和本地投影编排。"""

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field

from rag.backends.kafka import KafkaConsumerClient, RetryPolicy

from .models import GroupResourceAcl, ResourceAcl
from .store import ResourceAclRepository


class AuthoritativeAclError(ValueError):
    """上游资源记录缺失 ACL 投影所需字段。"""


class AclRecalculateEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    resource_id: str = Field(alias="resourceId", min_length=1)


class AclSynchronizer:
    """消费 ACL 刷新事件并完整替换本地资源 ACL。"""

    def __init__(
        self,
        *,
        collection: Any,
        repository: ResourceAclRepository,
    ) -> None:
        self._collection = collection
        self._repository = repository

    async def handle_event(self, payload: Mapping[str, Any]) -> list[str]:
        event = AclRecalculateEvent.model_validate(payload)
        return await self.synchronize([event.resource_id])

    async def synchronize(self, resource_ids: Sequence[str]) -> list[str]:
        unique_resource_ids = list(dict.fromkeys(resource_ids))
        source_acls = await self._read_authoritative(unique_resource_ids)

        synchronized: list[str] = []
        for resource_id in unique_resource_ids:
            resource_acl = source_acls.get(resource_id)
            # 权威记录已不存在：删除本地投影，不计入同步成功列表
            if resource_acl is None:
                await self._repository.delete(resource_id)
                continue
            if await self._repository.save(resource_acl):
                synchronized.append(resource_id)
        return synchronized

    async def _read_authoritative(
        self, resource_ids: Sequence[str]
    ) -> dict[str, ResourceAcl]:
        if not resource_ids:
            return {}
        if any(not ObjectId.is_valid(resource_id) for resource_id in resource_ids):
            raise AuthoritativeAclError("resource_id must be a valid ObjectId")

        records = await self._collection.find(
            {"_id": {"$in": [ObjectId(resource_id) for resource_id in resource_ids]}}
        ).to_list(length=None)
        # 只保留请求集合内、且能从 _id 还原出资源 ID 的记录
        return {
            resource_id: _project(record, resource_id)
            for record in records
            if (resource_id := str(record["_id"])) in resource_ids
        }


class AclEventConsumer:
    """用 RAG 共用消费者接收 ACL 重算事件，投影成功后才确认消息。"""

    def __init__(
        self,
        *,
        projection: AclSynchronizer,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        max_poll_interval_ms: int = 300_000,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._projection = projection
        self._consumer = KafkaConsumerClient(
            bootstrap_servers=bootstrap_servers,
            topic=topic,
            group_id=group_id,
            handler=projection.handle_event,
            max_poll_interval_ms=max_poll_interval_ms,
            retry_policy=retry_policy,
            is_retryable=lambda error: not isinstance(error, ValueError),
        )

    async def run(self, *, started: asyncio.Event | None = None) -> None:
        await self._consumer.run(started=started)


def _has_view(mask: Any) -> bool:
    """判断权限位掩码是否包含 VIEW（bit 1）；bool 视为非法掩码。"""
    return isinstance(mask, int) and not isinstance(mask, bool) and mask & (1 << 1) != 0


def _read_user_masks(value: Any) -> tuple[list[str], list[str]]:
    """把用户掩码拆成（可读白名单, 排除名单），跳过格式非法的条目。"""
    if not isinstance(value, dict):
        return [], []

    readable_users: list[str] = []
    excluded_read_users: list[str] = []
    for user_id, mask in value.items():
        if not isinstance(user_id, str) or not user_id.strip():
            continue
        if isinstance(mask, bool) or not isinstance(mask, int):
            continue
        target = readable_users if _has_view(mask) else excluded_read_users
        target.append(user_id.strip())
    return readable_users, excluded_read_users


def _read_group_acls(value: Any) -> list[GroupResourceAcl]:
    """把权威记录中的 computedGroupAcls 投影为本地群组 ACL。"""
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
        # 群组默认可读时，群组级白名单/排除名单不再有意义
        result.append(
            GroupResourceAcl(
                group_id=group_id.strip(),
                default_readable=default_readable,
                readable_users=[] if default_readable else readable_users,
                excluded_read_users=[] if default_readable else excluded_read_users,
            )
        )
    return result


def _project(record: Mapping[str, Any], resource_id: str) -> ResourceAcl:
    """把一条权威资源记录投影为本地 ResourceAcl。"""
    owner_id = record.get("ownerId")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise AuthoritativeAclError("ownerId must be a non-empty string")

    update_time = record.get("updateTime")
    if not isinstance(update_time, datetime):
        raise AuthoritativeAclError("updateTime must be a datetime")
    # 朴素时间按 UTC 处理，毫秒时间戳同时作为 ACL 版本号
    if update_time.tzinfo is None:
        update_time = update_time.replace(tzinfo=UTC)

    readable_users, excluded_read_users = _read_user_masks(
        record.get("specifiedUsersGrantedActionsMask")
    )
    return ResourceAcl(
        resource_id=resource_id,
        acl_revision=int(update_time.timestamp() * 1000),
        owner_id=owner_id.strip(),
        readable_users=readable_users,
        excluded_read_users=excluded_read_users,
        group_acls=_read_group_acls(record.get("computedGroupAcls")),
    )
