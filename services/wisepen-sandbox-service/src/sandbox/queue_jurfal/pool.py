from __future__ import annotations

from sandbox.queue_jurfal.models import PoolSnapshot, SandboxLease, SandboxRecord
from sandbox.queue_jurfal.repository import InMemorySandboxRepository


class SandboxPool:
    def __init__(
        self,
        repository: InMemorySandboxRepository,
        lease_ttl_seconds: int = 1800,
        min_ready: int = 1,
        target_ready: int = 2,
    ) -> None:
        self._repository = repository
        self._lease_ttl = lease_ttl_seconds
        self._min_ready = min_ready
        self._target_ready = target_ready

    async def add_ready(self, record: SandboxRecord) -> None:
        """Register a test/development warming record through the normal gate."""
        if await self._repository.get(record.ref.sandbox_id) is None:
            await self._repository.save(record)
        token = f"{record.ref.sandbox_id}:{record.state_version}:{record.fencing_token}"
        generation = await self._repository.prepare_ready(record, token)
        await self._repository.return_ready(record.ref.sandbox_id, token, generation)

    async def checkout(
        self, request_id: str, tenant_id: str, workspace_id: str
    ) -> tuple[SandboxRecord, SandboxLease]:
        record, lease = await self._repository.checkout_ready(
            request_id,
            tenant_id,
            workspace_id,
            self._lease_ttl,
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
        return f"{record.ref.sandbox_id}:{record.state_version}:{record.fencing_token}"

    async def prepare_readiness(self, record: SandboxRecord) -> tuple[str, int]:
        token = await self.health_token(record)
        generation = await self._repository.prepare_ready(record, token)
        return token, generation
