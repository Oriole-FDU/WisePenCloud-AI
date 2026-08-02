from __future__ import annotations

from sandbox.domain.entities import PoolSnapshot, SandboxLease, SandboxRecord, SandboxState
from sandbox.domain.repositories import SandboxRepository


class SandboxPool:
    """面向调度器的预热池门面。

    Pool 不直接保存状态，所有并发控制和状态转移都落在 Repository 中；
    这里只负责把“取 READY、放回 READY、读取快照”等用例收束成更小的接口。
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

    async def checkout(
        self, request_id: str, tenant_id: str, workspace_id: str
    ) -> tuple[SandboxRecord, SandboxLease]:
        # 取出 READY 实例时同时完成 request 幂等绑定、租约生成和 READY -> ALLOCATED。
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
