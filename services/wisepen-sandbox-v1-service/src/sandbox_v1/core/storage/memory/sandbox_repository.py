from __future__ import annotations

import uuid
from datetime import datetime
from typing import Iterable

from common.core.exceptions import ServiceException

from sandbox_v1.core.observability.metrics import MetricsCollector
from sandbox_v1.core.storage.memory.state import _RepositoryState
from sandbox_v1.domain.entities import (
    PoolSnapshot,
    SandboxRecord,
    SandboxState,
    UserSandboxBindingRecord,
    utc_now,
)
from sandbox_v1.domain.error_codes import SandboxErrorCode
from sandbox_v1.domain.interfaces.metrics import MetricsPort


# Repository 状态机白名单；transition 必须同时满足 expected 状态和目标状态合法。
_ALLOWED_TRANSITIONS: dict[SandboxState, frozenset[SandboxState]] = {
    SandboxState.CREATING: frozenset({SandboxState.WARMING, SandboxState.DESTROYING}),
    SandboxState.WARMING: frozenset({SandboxState.READY, SandboxState.DESTROYING}),
    SandboxState.READY: frozenset({SandboxState.USER_ACTIVE, SandboxState.DESTROYING}),
    SandboxState.USER_ACTIVE: frozenset({SandboxState.RETIRING, SandboxState.DESTROYING}),
    SandboxState.RETIRING: frozenset({SandboxState.DESTROYING}),
    SandboxState.DESTROYING: frozenset({SandboxState.DESTROYED, SandboxState.LOST}),
    SandboxState.DESTROYED: frozenset(),
    SandboxState.LOST: frozenset(),
}


class MemorySandboxRepository:
    """进程内的沙箱池权威存储。

    它同时保存池记录和用户绑定，所有读写都通过同一个 asyncio lock 保护。当前
    实现用于 v1 core 早期阶段，未来可由 Mongo 等持久化 Repository 替换同一端口。
    """

    def __init__(self, metrics: MetricsPort | None = None) -> None:
        state = _RepositoryState(metrics=metrics or MetricsCollector())
        self._state = state

    @property
    def metrics(self) -> MetricsPort:
        return self._state.metrics

    def _new_binding(
        self, sandbox_id: str, user_id: str
    ) -> UserSandboxBindingRecord:
        """为用户和沙箱创建一条稳定绑定，并写入双向索引。"""

        # user -> sandbox 记录用于复用，sandbox -> user 反向索引用于后续恢复/排查。
        binding = UserSandboxBindingRecord(
            user_binding_id=f"user_{uuid.uuid4().hex}",
            sandbox_id=sandbox_id,
            user_id=user_id,
        )
        self._state.user_bindings[user_id] = binding
        self._state.sandbox_bindings[sandbox_id] = user_id
        self._state.metrics.increment("user_bindings_created")
        return binding

    async def save(self, record: SandboxRecord) -> None:
        """保存或覆盖一条沙箱记录，并推进 Repository generation。"""

        async with self._state.lock:
            self._state.records[record.ref.sandbox_id] = record
            # generation 标记可观测状态变化，供快照消费者判断池状态是否更新。
            self._state.generation += 1

    async def get(self, sandbox_id: str) -> SandboxRecord | None:
        """按 sandbox_id 读取当前权威记录。"""

        async with self._state.lock:
            return self._state.records.get(sandbox_id)

    async def records_in(
        self, states: Iterable[SandboxState]
    ) -> list[SandboxRecord]:
        """读取处于指定状态集合内的沙箱记录。"""

        # states 可能是 tuple/list/generator，先转 set 便于多次 membership 判断。
        wanted = set(states)
        async with self._state.lock:
            return [
                record
                for record in self._state.records.values()
                if record.state in wanted
            ]

    async def snapshot(
        self, *, min_ready: int = 0, target_ready: int = 0
    ) -> PoolSnapshot:
        """生成当前池状态快照，并合并指标收集器输出。"""

        async with self._state.lock:
            # 先按所有状态补齐计数，避免消费者处理缺失 key。
            counts = {state: 0 for state in SandboxState}
            for record in self._state.records.values():
                counts[record.state] += 1

            # active_user_bindings 是从权威记录反推的当前用户态数量。
            ready = counts[SandboxState.READY]
            self._state.metrics.set_value(
                "active_user_bindings", counts[SandboxState.USER_ACTIVE]
            )
            # PoolSnapshot 同时携带状态计数、空池 checkout 次数和派生指标。
            return PoolSnapshot(
                generation=self._state.generation,
                counts=counts,
                empty_checkouts=self._state.empty_checkouts,
                metrics=self._state.metrics.snapshot(
                    ready, min_ready, target_ready
                ),
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
        """按 expected-state 语义执行一次合法状态转换。"""

        async with self._state.lock:
            record = self._state.records.get(sandbox_id)
            if record is None:
                raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE,f"sandbox {sandbox_id} does not exist")
            # transition 既要求当前状态等于 expected，也要求目标状态在白名单内。
            if record.state != expected or state not in _ALLOWED_TRANSITIONS[expected]:
                raise ServiceException(SandboxErrorCode.INVALID_STATE_TRANSITION,f"cannot transition {record.state.value} to {state.value}")
            # 状态变化后推进版本、更新时间和错误上下文。
            record.state = state
            record.state_version += 1
            record.updated_at = utc_now()
            record.last_error = error
            self._state.generation += 1
            return record

    async def checkout_ready(
        self,
        user_id: str,
        max_user_bindings: int = 20,
    ) -> SandboxRecord:
        """分配一个 READY 容器给用户，或复用用户已有绑定。"""

        async with self._state.lock:
            binding = self._state.user_bindings.get(user_id)
            if binding is None:
                # 首次 checkout 需要先检查全局用户绑定容量上限。
                if len(self._state.user_bindings) >= max_user_bindings:
                    raise ServiceException(SandboxErrorCode.USER_SANDBOX_CAPACITY,"user sandbox capacity has been reached")
                # 从 READY 池中挑选一个可消费容器；没有 READY 时记录空池指标。
                record = next(
                    (
                        item
                        for item in self._state.records.values()
                        if item.state == SandboxState.READY
                    ),
                    None,
                )
                if record is None:
                    self._state.empty_checkouts += 1
                    self._state.metrics.increment("pool_empty_checkouts")
                    raise ServiceException(SandboxErrorCode.POOL_EMPTY,"sandbox pool has no READY container")
                # 消费动作只做稳定绑定，不记录每次操作级 ownership 或幂等请求状态。
                record.state = SandboxState.USER_ACTIVE
                record.state_version += 1
                binding = self._new_binding(record.ref.sandbox_id, user_id)
                record.owner_user_id = user_id
                record.user_binding_id = binding.user_binding_id
            else:
                # 已有绑定时复用同一个用户容器，保持用户工作区稳定。
                record = self._state.records.get(binding.sandbox_id)
                if record is None:
                    raise ServiceException(
                        SandboxErrorCode.SANDBOX_UNAVAILABLE,
                        "user binding points to a missing container",
                    )
                if record.state not in {SandboxState.USER_ACTIVE}:
                    raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE,"user container is not available")
                binding.reuse_count += 1
                record.reuse_count = binding.reuse_count
                self._state.metrics.increment("user_container_reuse_hits")

            # 更新记录和绑定活跃时间，并推进 generation。
            now = utc_now()
            record.updated_at = now
            binding.updated_at = now
            binding.last_active_at = now
            self._state.generation += 1
            return record

    async def records_older_than(
        self, state: SandboxState, cutoff: datetime
    ) -> list[SandboxRecord]:
        """读取指定状态下 updated_at 不晚于 cutoff 的陈旧记录。"""

        async with self._state.lock:
            return [
                record
                for record in self._state.records.values()
                if record.state == state and record.updated_at <= cutoff
            ]
