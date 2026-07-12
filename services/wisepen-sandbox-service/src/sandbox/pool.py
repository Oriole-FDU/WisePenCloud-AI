from __future__ import annotations

from sandbox.models import PoolSnapshot, SandboxLease, SandboxRecord, SandboxState, utc_now
from sandbox.repository import InMemorySandboxRepository


class SandboxPool:
    def __init__(
        self, repository: InMemorySandboxRepository, lease_ttl_seconds: int = 1800
    ) -> None:
        self._repository = repository
        self._lease_ttl = lease_ttl_seconds

    async def add_ready(self, record: SandboxRecord) -> None:
        if await self._repository.get(record.ref.sandbox_id) is None:
            await self._repository.save(record)
        await self._repository.transition(
            record.ref.sandbox_id,
            SandboxState.WARMING,
            SandboxState.READY,
        )

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
        return await self._repository.snapshot()

    async def mark_creating(self, record: SandboxRecord) -> None:
        await self._repository.save(record)

    async def health_token(self, record: SandboxRecord) -> str:
        return f"{record.ref.sandbox_id}:{record.state_version}:{record.fencing_token}"
