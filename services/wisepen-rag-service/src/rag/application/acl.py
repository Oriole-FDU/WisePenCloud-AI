"""ACL 事件、权威事实读取和本地投影编排。"""

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field

from rag.domain.messaging import MessageConsumer
from rag.domain.models.acl import GroupResourceAcl, ResourceAcl
from rag.domain.storage.acl import ResourceAclRepository


class AuthoritativeAclError(ValueError):
    """上游资源记录的必需字段缺失或 ACL 格式不符合合同。"""


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
        # Mongo ObjectId 使用规范小写表示，输入与返回身份必须在同一边界统一。
        if any(not ObjectId.is_valid(resource_id) for resource_id in resource_ids):
            raise AuthoritativeAclError("resource_id must be a valid ObjectId")
        unique_resource_ids = list(
            dict.fromkeys(str(ObjectId(key)) for key in resource_ids)
        )
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
        records = await self._collection.find(
            {"_id": {"$in": [ObjectId(resource_id) for resource_id in resource_ids]}}
        ).to_list(length=None)
        # 查询范围由 Mongo 保证，不再用字符串比较静默过滤结果。
        return {
            str(record["_id"]): _project(record, str(record["_id"]))
            for record in records
        }


class AclEventConsumer:
    """将 ACL 事件投影处理器交给共用消息消费者运行。"""

    def __init__(
        self,
        *,
        projection: AclSynchronizer,
        consumer: MessageConsumer,
    ) -> None:
        self._projection = projection
        self._consumer = consumer

    async def run(self, *, started: asyncio.Event | None = None) -> None:
        await self._consumer.run(self._projection.handle_event, started=started)


def _has_view(mask: int) -> bool:
    return mask & (1 << 1) != 0


def _read_user_masks(
    value: Any, *, resource_id: str, field: str
) -> tuple[list[str], list[str]]:
    """解析权威用户掩码；合法缺省由调用方归一化，非法条目拒绝整个投影。"""
    if not isinstance(value, dict):
        raise AuthoritativeAclError(f"{resource_id}: {field} must be an object")

    readable_users: list[str] = []
    excluded_read_users: list[str] = []
    for user_id, mask in value.items():
        if not isinstance(user_id, str) or not user_id.strip():
            raise AuthoritativeAclError(
                f"{resource_id}: {field} contains a blank user ID"
            )
        if type(mask) is not int:
            raise AuthoritativeAclError(
                f"{resource_id}: {field}.{user_id} must be an integer"
            )
        target = readable_users if _has_view(mask) else excluded_read_users
        target.append(user_id)
    return readable_users, excluded_read_users


def _read_group_acls(
    value: Any, *, resource_id: str, market_versions: dict[str, int]
) -> list[GroupResourceAcl]:
    """把权威记录中的 computedGroupAcls 投影为本地群组 ACL。"""
    if not isinstance(value, dict):
        raise AuthoritativeAclError(
            f"{resource_id}: computedGroupAcls must be an object"
        )

    result: list[GroupResourceAcl] = []
    for group_id, group_value in value.items():
        if not isinstance(group_id, str) or not group_id.strip():
            raise AuthoritativeAclError(
                f"{resource_id}: computedGroupAcls contains a blank group ID"
            )
        if not isinstance(group_value, dict):
            raise AuthoritativeAclError(
                f"{resource_id}: computedGroupAcls.{group_id} must be an object"
            )

        # 上游重算总是写入 baseMask/userMasks；已有组内缺项不表示合法空权限。
        field = f"computedGroupAcls.{group_id}"
        base_mask = group_value.get("baseMask")
        if type(base_mask) is not int:
            raise AuthoritativeAclError(
                f"{resource_id}: {field}.baseMask must be an integer"
            )
        default_readable = _has_view(base_mask)
        readable_users, excluded_read_users = _read_user_masks(
            group_value.get("userMasks"),
            resource_id=resource_id,
            field=f"{field}.userMasks",
        )
        # 上游 userMasks 覆盖 baseMask；默认可读时也必须保留 mask=0 的排除项。
        result.append(
            GroupResourceAcl(
                group_id=group_id,
                default_readable=default_readable,
                readable_users=readable_users,
                excluded_read_users=excluded_read_users,
                market_offer_version=market_versions.get(group_id),
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

    # 上游允许撤销指定用户权限时写 null；尚未重算的 computedGroupAcls 也可缺省。
    # 只归一化这两个顶层的缺失/null，错误类型绝不当作空 ACL。
    user_masks = record.get("specifiedUsersGrantedActionsMask")
    group_acls = record.get("computedGroupAcls")
    # 市场限制来自权威 groupBinds，不从权限掩码推断是否为市场组。
    group_binds = record.get("groupBinds")
    if group_binds is None:
        group_binds = []
    if not isinstance(group_binds, list):
        raise AuthoritativeAclError(f"{resource_id}: groupBinds must be a list")
    market_versions = {}
    for bind in group_binds:
        if not isinstance(bind, dict):
            raise AuthoritativeAclError(
                f"{resource_id}: groupBinds entries must be objects"
            )
        group_id = bind.get("groupId")
        if not isinstance(group_id, str) or not group_id.strip():
            raise AuthoritativeAclError(
                f"{resource_id}: groupBinds.groupId must be nonblank"
            )
        market = bind.get("marketSaleInfo")
        if market is not None:
            if (
                not isinstance(market, dict)
                or type(market.get("offerVersion")) is not int
            ):
                raise AuthoritativeAclError(
                    f"{resource_id}: groupBinds.{group_id}.marketSaleInfo requires an integer offerVersion"
                )
            market_versions[group_id] = market["offerVersion"]
    readable_users, excluded_read_users = _read_user_masks(
        {} if user_masks is None else user_masks,
        resource_id=resource_id,
        field="specifiedUsersGrantedActionsMask",
    )
    return ResourceAcl(
        resource_id=resource_id,
        acl_revision=int(update_time.timestamp() * 1000),
        owner_id=owner_id,
        readable_users=readable_users,
        excluded_read_users=excluded_read_users,
        group_acls=_read_group_acls(
            {} if group_acls is None else group_acls,
            resource_id=resource_id,
            market_versions=market_versions,
        ),
    )
