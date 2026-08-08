from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Iterable

from common.core.exceptions import ServiceException

from sandbox_v1.core.observability.metrics import MetricsCollector
from sandbox_v1.core.storage.mongo.documents import (
    sandbox_record_from_doc,
    sandbox_record_to_doc,
    user_binding_to_doc,
)
from sandbox_v1.domain.entities import (
    PoolSnapshot,
    SandboxRecord,
    SandboxState,
    UserSandboxBindingRecord,
    can_transition,
    utc_now,
)
from sandbox_v1.domain.error_codes import SandboxErrorCode
from sandbox_v1.domain.interfaces.metrics import MetricsPort


class MongoSandboxRepository:
    """Mongo-backed 的沙箱池记录与用户绑定权威存储。

    Docker/provider labels 仍只是发现提示；READY/USER_ACTIVE 状态、用户绑定、
    generation 和空池 checkout 计数都落在 Mongo 中，服务重启后不会遗忘当前池状态。
    """

    def __init__(
        self,
        *,
        database: Any,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._database = database
        self._sandboxes = database["wisepen_sandbox_v1_sandbox"]
        self._bindings = database["wisepen_sandbox_v1_user_binding"]
        self._meta = database["wisepen_sandbox_v1_meta"]
        self._metrics = metrics or MetricsCollector()

    @property
    def metrics(self) -> MetricsPort:
        return self._metrics

    async def initialize(self) -> None:
        """校验 Mongo 可用性，并创建 sandbox、binding、meta 相关索引。"""

        # ping 提前暴露连接或权限问题，避免服务启动后才在业务请求中失败。
        await self._database.command("ping")
        # sandbox_id 是领域主键，同时作为 document _id 的语义等价字段。
        await self._sandboxes.create_index(
            [("sandbox_id", 1)],
            unique=True,
            name="uniq_sandbox_id",
        )
        # provider_id/state 索引用于启动发现、状态查询和 stale cleanup。
        await self._sandboxes.create_index(
            [("provider_id", 1)],
            name="idx_provider_id",
        )
        await self._sandboxes.create_index(
            [("state", 1), ("updated_at", 1)],
            name="idx_state_updated_at",
        )
        # user_id 和 sandbox_id 都保持唯一，保证一个用户只绑定一个活跃容器。
        await self._bindings.create_index(
            [("user_id", 1)],
            unique=True,
            name="uniq_user_id",
        )
        await self._bindings.create_index(
            [("sandbox_id", 1)],
            unique=True,
            name="uniq_binding_sandbox_id",
        )
        # meta document 保存 pool generation 和 empty checkout 这类聚合计数。
        await self._meta.update_one(
            {"_id": "pool"},
            {"$setOnInsert": {"generation": 0, "empty_checkouts": 0}},
            upsert=True,
        )

    async def save(self, record: SandboxRecord) -> None:
        """保存或覆盖一条 sandbox 记录，并推进 pool generation。"""

        await self._sandboxes.replace_one(
            {"sandbox_id": record.ref.sandbox_id},
            sandbox_record_to_doc(record),
            upsert=True,
        )
        await self._inc_generation()

    async def get(self, sandbox_id: str) -> SandboxRecord | None:
        """按 sandbox_id 读取 sandbox 权威记录。"""

        doc = await self._sandboxes.find_one({"sandbox_id": sandbox_id})
        return sandbox_record_from_doc(doc) if doc is not None else None

    async def records_in(
        self,
        states: Iterable[SandboxState],
    ) -> list[SandboxRecord]:
        """读取处于指定状态集合内的 sandbox 记录。"""

        state_values = [state.value for state in states]
        cursor = self._sandboxes.find({"state": {"$in": state_values}})
        return [sandbox_record_from_doc(doc) async for doc in cursor]

    async def snapshot(
        self,
        *,
        min_ready: int = 0,
        target_ready: int = 0,
    ) -> PoolSnapshot:
        """聚合 Mongo 中的 sandbox 状态计数，并返回池快照。"""

        # 先按所有状态补齐计数，避免快照消费者处理缺失 key。
        counts = {state: 0 for state in SandboxState}
        cursor = self._sandboxes.find({}, {"state": 1})
        async for doc in cursor:
            counts[SandboxState(doc["state"])] += 1

        # meta 保存跨进程持久化的 generation 和 empty_checkout 计数。
        meta = await self._meta.find_one({"_id": "pool"}) or {}
        ready = counts[SandboxState.READY]
        # active_user_bindings 是从权威 sandbox 状态反推的当前用户态数量。
        self._metrics.set_value(
            "active_user_bindings",
            counts[SandboxState.USER_ACTIVE],
        )
        return PoolSnapshot(
            generation=int(meta.get("generation") or 0),
            counts=counts,
            empty_checkouts=int(meta.get("empty_checkouts") or 0),
            metrics=self._metrics.snapshot(ready, min_ready, target_ready),
            min_ready=min_ready,
            target_ready=target_ready,
        )

    async def transition(
        self,
        sandbox_id: str,
        expected: SandboxState,
        state: SandboxState,
        *,
        error: str | None = None,
    ) -> SandboxRecord:
        """按 expected-state CAS 语义执行一次合法状态转换。"""

        # 先在领域层检查目标转换是否合法，再交给 Mongo 做 expected-state CAS。
        if not can_transition(expected, state):
            raise ServiceException(
                SandboxErrorCode.INVALID_STATE_TRANSITION,
                f"cannot transition {expected.value} to {state.value}",
        )

        now = utc_now()
        # 查询条件包含当前 state，保证只有 expected 状态才能被更新。
        updated = await self._sandboxes.find_one_and_update(
            {"sandbox_id": sandbox_id, "state": expected.value},
            {
                "$set": {
                    "state": state.value,
                    "updated_at": now,
                    "last_error": error,
                },
                "$inc": {"state_version": 1},
            },
            return_document=True,
        )
        if updated is None:
            # 更新失败后再读取当前记录，区分“不存在”和“状态不匹配”。
            current = await self._sandboxes.find_one({"sandbox_id": sandbox_id})
            if current is None:
                raise ServiceException(
                    SandboxErrorCode.SANDBOX_UNAVAILABLE,
                    f"sandbox {sandbox_id} does not exist",
                )
            raise ServiceException(
                SandboxErrorCode.INVALID_STATE_TRANSITION,
                f"cannot transition {current['state']} to {state.value}",
            )

        await self._inc_generation()
        return sandbox_record_from_doc(updated)

    async def checkout_ready(
        self,
        user_id: str,
        max_user_bindings: int = 20,
    ) -> SandboxRecord:
        """为用户分配 READY 容器，或复用已有 USER_ACTIVE 绑定。"""

        # 用户已有绑定时优先复用，保持用户工作区/容器稳定。
        binding = await self._bindings.find_one({"user_id": user_id})
        if binding is not None:
            return await self._reuse_binding(binding)

        # 新绑定前检查容量上限。
        if await self._bindings.count_documents({}) >= max_user_bindings:
            raise ServiceException(
                SandboxErrorCode.USER_SANDBOX_CAPACITY,
                "user sandbox capacity has been reached",
            )

        now = utc_now()
        binding_id = f"user_{uuid.uuid4().hex}"
        # 原子领取一个最早创建的 READY 容器，并直接转为 USER_ACTIVE。
        record_doc = await self._sandboxes.find_one_and_update(
            {"state": SandboxState.READY.value},
            {
                "$set": {
                    "state": SandboxState.USER_ACTIVE.value,
                    "owner_user_id": user_id,
                    "user_binding_id": binding_id,
                    "updated_at": now,
                    "last_error": None,
                },
                "$inc": {"state_version": 1},
            },
            sort=[("created_at", 1)],
            return_document=True,
        )
        if record_doc is None:
            # 没有 READY 时持久化空池 checkout 计数，并抛出业务错误。
            await self._meta.update_one(
                {"_id": "pool"},
                {"$inc": {"empty_checkouts": 1}},
                upsert=True,
            )
            self._metrics.increment("pool_empty_checkouts")
            raise ServiceException(
                SandboxErrorCode.POOL_EMPTY,
                "sandbox pool has no READY container",
            )

        binding = UserSandboxBindingRecord(
            user_binding_id=binding_id,
            sandbox_id=record_doc["sandbox_id"],
            user_id=user_id,
            created_at=now,
            updated_at=now,
            last_active_at=now,
        )
        # 生产通常单实例运行；这里仍使用 setOnInsert，防止同用户并发请求重复创建绑定。
        await self._bindings.update_one(
            {"user_id": user_id},
            {"$setOnInsert": user_binding_to_doc(binding)},
            upsert=True,
        )
        self._metrics.increment("user_bindings_created")
        await self._inc_generation()
        return sandbox_record_from_doc(record_doc)

    async def records_older_than(
        self,
        state: SandboxState,
        cutoff: datetime,
    ) -> list[SandboxRecord]:
        """读取指定状态下 updated_at 不晚于 cutoff 的陈旧 sandbox 记录。"""

        cursor = self._sandboxes.find(
            {
                "state": state.value,
                "updated_at": {"$lte": cutoff},
            }
        )
        return [sandbox_record_from_doc(doc) async for doc in cursor]

    async def _reuse_binding(self, binding: dict[str, Any]) -> SandboxRecord:
        """复用已有用户绑定，并同步更新 binding 与 sandbox 的访问计数。"""

        now = utc_now()
        # 先刷新 binding 的访问时间和复用次数。
        updated_binding = await self._bindings.find_one_and_update(
            {"user_id": binding["user_id"]},
            {
                "$set": {
                    "updated_at": now,
                    "last_active_at": now,
                },
                "$inc": {"reuse_count": 1},
            },
            return_document=True,
        )
        # 再确认绑定指向的 sandbox 仍处于 USER_ACTIVE。
        record_doc = await self._sandboxes.find_one_and_update(
            {
                "sandbox_id": binding["sandbox_id"],
                "state": SandboxState.USER_ACTIVE.value,
            },
            {
                "$set": {
                    "updated_at": now,
                    "last_error": None,
                },
                "$inc": {"reuse_count": 1},
            },
            return_document=True,
        )
        if updated_binding is None or record_doc is None:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "user container is not available",
            )

        self._metrics.increment("user_container_reuse_hits")
        await self._inc_generation()
        return sandbox_record_from_doc(record_doc)

    async def _inc_generation(self) -> None:
        """推进 pool generation，供快照消费者观察状态变化。"""

        await self._meta.update_one(
            {"_id": "pool"},
            {"$inc": {"generation": 1}},
            upsert=True,
        )
