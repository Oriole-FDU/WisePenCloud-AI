from __future__ import annotations

from dataclasses import dataclass

from sandbox.domain.entities import PoolSnapshot, SandboxLease, SandboxRecord, SandboxState
from sandbox.domain.repositories import SandboxRepository


@dataclass(frozen=True)
class PoolMaintenancePlan:
    """一次容器池维持评估的结果。

    READY 是可立即消费的空闲容器；WARMING/CREATING 是已经在补充路径上的容器。
    三者都要计入供给，避免 watcher 在预热尚未完成时重复创建过多容器。
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
        ready = snapshot.counts.get(SandboxState.READY, 0)
        warming = snapshot.counts.get(SandboxState.WARMING, 0)
        creating = snapshot.counts.get(SandboxState.CREATING, 0)
        reserve = max(0, reserve)
        max_create_batch = max(1, max_create_batch)
        deficit = max(
            0,
            snapshot.target_ready + reserve - ready - warming - creating,
        )
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

    @property
    def should_replenish(self) -> bool:
        """是否需要 watcher 在本轮执行补充。"""

        return self.create_count > 0


class SandboxPool:
    """面向调度器的预热池门面。

    Pool 不直接保存状态，所有并发控制和状态转移都落在 Repository 中；
    这里只负责把“维持容量、消费 READY、放回 READY”等用例收束成更小的接口。
    """

    def __init__(
        self,
        repository: SandboxRepository,
        lease_ttl_seconds: int = 1800,
        min_ready: int = 1,
        target_ready: int = 2,
        user_idle_ttl_seconds: int = 600,
        max_user_bindings: int = 20,
    ) -> None:
        self._repository = repository
        self._lease_ttl = lease_ttl_seconds
        self._min_ready = min_ready
        self._target_ready = target_ready
        self._user_idle_ttl = user_idle_ttl_seconds
        self._max_user_bindings = max_user_bindings

    async def add_ready(self, record: SandboxRecord) -> None:
        """通过正常 readiness gate 注册测试/开发用预热实例。"""
        if await self._repository.get(record.ref.sandbox_id) is None:
            await self._repository.save(record)
        token = f"{record.ref.sandbox_id}:{record.state_version}"
        generation = await self._repository.prepare_ready(record, token)
        await self._repository.return_ready(record.ref.sandbox_id, token, generation)

    async def consume(
        self, request_id: str, tenant_id: str, workspace_id: str
    ) -> tuple[SandboxRecord, SandboxLease]:
        # 消费 READY 必须是一个原子动作：Repository 同时完成 request 幂等绑定、
        # 用户容器绑定、租约生成和 READY -> ALLOCATED，避免两个调用拿到同一容器。
        record, lease = await self._repository.checkout_ready(
            request_id,
            tenant_id,
            workspace_id,
            self._lease_ttl,
            self._user_idle_ttl,
            self._max_user_bindings,
        )
        return record, lease.as_lease()

    async def snapshot(self) -> PoolSnapshot:
        return await self._repository.snapshot(
            min_ready=self._min_ready,
            target_ready=self._target_ready,
        )

    async def maintenance_plan(
        self, *, reserve: int = 0, max_create_batch: int = 1
    ) -> PoolMaintenancePlan:
        """计算本轮补池计划，不直接创建容器。

        维持逻辑集中在 Pool，Watcher 只执行计划；这样 consume 消费 READY 后，
        下一轮 watcher 会基于同一套规则发现缺口并补充。
        """

        snapshot = await self.snapshot()
        return PoolMaintenancePlan.from_snapshot(
            snapshot,
            reserve=reserve,
            max_create_batch=max_create_batch,
        )

    async def return_ready(
        self, sandbox_id: str, health_token: str, expected_generation: int
    ) -> SandboxRecord:
        return await self._repository.return_ready(
            sandbox_id, health_token, expected_generation
        )

    async def mark_creating(self, record: SandboxRecord) -> None:
        await self._repository.save(record)

    async def health_token(self, record: SandboxRecord) -> str:
        # 健康 token 绑定 sandbox_id、状态版本和 fencing，避免过期健康检查误放回实例。
        return f"{record.ref.sandbox_id}:{record.state_version}"

    async def prepare_readiness(self, record: SandboxRecord) -> tuple[str, int]:
        token = await self.health_token(record)
        generation = await self._repository.prepare_ready(record, token)
        return token, generation
