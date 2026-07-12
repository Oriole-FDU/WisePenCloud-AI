from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

from sandbox.errors import PoolEmptyError
from sandbox.models import SandboxLease, SandboxRecord, SandboxState, utc_now
from sandbox.repository import InMemorySandboxRepository


class SandboxPool:
    def __init__(
        self, repository: InMemorySandboxRepository, lease_ttl_seconds: int = 1800
    ) -> None:
        self._repository = repository
        self._lease_ttl = lease_ttl_seconds
        self._lock = asyncio.Lock()

    async def add_ready(self, record: SandboxRecord) -> None:
        record.state = SandboxState.READY
        record.lease_id = None
        record.updated_at = utc_now()
        await self._repository.save(record)

    async def checkout(
        self, request_id: str, tenant_id: str, workspace_id: str
    ) -> tuple[SandboxRecord, SandboxLease]:
        async with self._lock:
            existing = await self._repository.find_request(request_id)
            if existing and existing.lease_id:
                return existing, self._lease(existing, request_id, tenant_id, workspace_id)

            ready = await self._repository.records_in([SandboxState.READY])
            if not ready:
                raise PoolEmptyError("no ready sandbox is available")
            record = ready[0]
            now = utc_now()
            record.state = SandboxState.ALLOCATED
            record.lease_id = f"lease_{uuid.uuid4().hex}"
            record.request_id = request_id
            record.tenant_id = tenant_id
            record.workspace_id = workspace_id
            record.lease_expires_at = now + timedelta(seconds=self._lease_ttl)
            record.updated_at = now
            await self._repository.save(record)
            await self._repository.bind_request(request_id, record.ref.sandbox_id)
            return record, self._lease(record, request_id, tenant_id, workspace_id)

    def _lease(
        self, record: SandboxRecord, request_id: str, tenant_id: str, workspace_id: str
    ) -> SandboxLease:
        return SandboxLease(
            lease_id=record.lease_id or "",
            request_id=request_id,
            sandbox_id=record.ref.sandbox_id,
            tenant_id=record.tenant_id or tenant_id,
            workspace_id=record.workspace_id or workspace_id,
            expires_at=record.lease_expires_at or utc_now(),
            endpoint=record.ref.endpoint,
        )

    async def snapshot(self) -> dict[str, int]:
        counts = await self._repository.counts()
        return {state.value: counts[state] for state in SandboxState}
