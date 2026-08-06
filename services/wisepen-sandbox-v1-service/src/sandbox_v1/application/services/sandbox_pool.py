from __future__ import annotations

from dataclasses import dataclass

from common.core.exceptions import ServiceException

from sandbox_v1.domain.entities import (
    PoolSnapshot,
    SandboxRecord,
    SandboxState,
)
from sandbox_v1.domain.error_codes import SandboxErrorCode
from sandbox_v1.domain.repositories import SandboxRepository


@dataclass(frozen=True)
class PoolMaintenancePlan:
    """一次池容量评估的结果。

    READY 是可立即消费的供给，WARMING/CREATING 是已经在路上的供给。计算
    deficit 时会把这些 in-flight 容器一起扣除，避免 watcher 在预热未完成时
    重复创建。create_count 是经过 max_create_batch 限制后的本轮实际创建数量。
    """

    ready: int
    warming: int
    creating: int
    target_ready: int
    reserve: int
    max_create_batch: int
    deficit: int
    create_count: int

    @classmethod
    def from_snapshot(
        cls,
        snapshot: PoolSnapshot,
        *,
        reserve: int,
        max_create_batch: int,
    ) -> "PoolMaintenancePlan":
        """从当前池快照计算本轮 watcher 需要创建的容器数量。"""

        # 读取当前池供给：READY 可消费，WARMING/CREATING 是正在补充的供给。
        ready = snapshot.counts.get(SandboxState.READY, 0)
        warming = snapshot.counts.get(SandboxState.WARMING, 0)
        creating = snapshot.counts.get(SandboxState.CREATING, 0)

        # 规整策略参数，保证 reserve 非负且单轮至少允许创建 1 个。
        reserve = max(0, reserve)
        max_create_batch = max(1, max_create_batch)

        # 计算缺口时把 in-flight 容器计入供给，避免同一缺口被重复创建。
        deficit = max(
            0,
            snapshot.target_ready + reserve - ready - warming - creating,
        )

        # create_count 是本轮实际动作数量，受 max_create_batch 保护。
        return cls(
            ready=ready,
            warming=warming,
            creating=creating,
            target_ready=snapshot.target_ready,
            reserve=reserve,
            max_create_batch=max_create_batch,
            deficit=deficit,
            create_count=min(deficit, max_create_batch),
        )


class SandboxPool:
    """容器池能力门面。

    Pool 负责用户消费、快照读取和容量计划计算；它不直接创建或销毁容器，也不
    自己维护 ownership 状态，具体状态机动作委托给 Repository 和 Watcher。
    """

    def __init__(
        self,
        repository: SandboxRepository,
        min_ready: int = 1,
        target_ready: int = 2,
        max_user_bindings: int = 20,
    ) -> None:
        self._repository = repository

        # 池策略配置：最低 READY、目标 READY，以及单用户绑定容量上限。
        self._min_ready = min_ready
        self._target_ready = target_ready
        self._max_user_bindings = max_user_bindings

    async def consume(self, user_id: str) -> SandboxRecord:
        """校验用户标识，并为用户分配或复用一个 READY 容器。

        实际 checkout、状态转移和用户绑定复用由 Repository 完成，Pool 不维护
        每次操作的 ownership 状态。
        """

        # 拒绝空用户标识，避免 Repository 生成不可追踪的用户绑定。
        if not user_id or not user_id.strip():
            raise ServiceException(SandboxErrorCode.INVALID_CONSUME_REQUEST,"user identifier is required")

        # 委托 Repository checkout，保持 Pool 只作为能力门面。
        return await self._repository.checkout_ready(user_id, self._max_user_bindings)

    async def snapshot(self) -> PoolSnapshot:
        """返回带 min/target 配置的池快照，供 API、健康检查和补池计划使用。"""

        return await self._repository.snapshot(min_ready=self._min_ready, target_ready=self._target_ready)

    async def maintenance_plan(
        self, *, reserve: int = 0, max_create_batch: int = 1
    ) -> PoolMaintenancePlan:
        """基于当前池快照生成本轮 watcher 补池计划。"""

        # 先读取当前池状态，再按 reserve 和 batch 策略计算创建数量。
        snapshot = await self.snapshot()
        return PoolMaintenancePlan.from_snapshot(
            snapshot,
            reserve=reserve,
            max_create_batch=max_create_batch,
        )
