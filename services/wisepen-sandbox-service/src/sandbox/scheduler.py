from __future__ import annotations

from datetime import datetime, timezone
import asyncio

from sandbox.errors import LeaseExpiredError, LeaseNotFoundError, SandboxDomainError
from sandbox.models import (
    ExecutionRequest,
    ExecutionResult,
    SandboxLease,
    SandboxRecord,
    SandboxState,
    utc_now,
)
from sandbox.ports import SandboxProvider, WorkspaceStore
from sandbox.pool import SandboxPool
from sandbox.repository import InMemorySandboxRepository


class SandboxScheduler:
    def __init__(
        self,
        pool: SandboxPool,
        repository: InMemorySandboxRepository,
        provider: SandboxProvider,
        workspace_store: WorkspaceStore,
    ) -> None:
        self._pool = pool
        self._repository = repository
        self._provider = provider
        self._workspace_store = workspace_store
        self._allocate_lock = asyncio.Lock()
        self._released_leases: set[str] = set()

    async def allocate(
        self, request_id: str, tenant_id: str, workspace_id: str
    ) -> SandboxLease:
        async with self._allocate_lock:
            record, lease = await self._pool.checkout(
                request_id, tenant_id, workspace_id
            )
            if record.state == SandboxState.RUNNING:
                return lease
            try:
                workspace = await self._workspace_store.snapshot(tenant_id, workspace_id)
                await self._provider.prepare_workspace(record.ref, workspace)
                endpoint = await self._provider.activate(record.ref, lease)
                record.ref = type(record.ref)(
                    sandbox_id=record.ref.sandbox_id,
                    provider_id=record.ref.provider_id,
                    endpoint=endpoint,
                    metadata=record.ref.metadata,
                )
                record.state = SandboxState.RUNNING
                record.updated_at = utc_now()
                await self._repository.save(record)
                return SandboxLease(
                    lease_id=lease.lease_id,
                    request_id=lease.request_id,
                    sandbox_id=lease.sandbox_id,
                    tenant_id=lease.tenant_id,
                    workspace_id=lease.workspace_id,
                    expires_at=lease.expires_at,
                    endpoint=endpoint,
                )
            except Exception:
                await self._destroy_record(record, "allocation_failed")
                raise

    async def execute(
        self, lease_id: str, request: ExecutionRequest
    ) -> ExecutionResult:
        record = await self._find_lease(lease_id)
        if record.state != SandboxState.RUNNING:
            raise SandboxDomainError("sandbox lease is not running")
        if record.lease_expires_at and record.lease_expires_at <= datetime.now(timezone.utc):
            raise LeaseExpiredError("sandbox lease has expired")
        if record.tenant_id != request.tenant_id:
            raise SandboxDomainError("tenant does not own lease")
        return await self._provider.forward(record.ref, request)

    async def release(self, lease_id: str) -> None:
        if lease_id in self._released_leases:
            return
        record = await self._find_lease(lease_id)
        if record.state == SandboxState.DESTROYED:
            return
        if record.state in (SandboxState.DESTROYING, SandboxState.LOST):
            return
        if record.state not in (SandboxState.RUNNING, SandboxState.ALLOCATED):
            raise SandboxDomainError(f"cannot release {record.state.value} sandbox")

        record.state = SandboxState.SYNCING
        record.updated_at = utc_now()
        await self._repository.save(record)
        try:
            snapshot = await self._provider.export_workspace(
                record.ref, record.tenant_id or "", record.workspace_id or ""
            )
            await self._workspace_store.commit(snapshot, record.lease_id or lease_id)
        finally:
            await self._destroy_record(record, "lease_released")
            self._released_leases.add(lease_id)

    async def _find_lease(self, lease_id: str) -> SandboxRecord:
        records = await self._repository.records_in(
            [
                SandboxState.ALLOCATED,
                SandboxState.RUNNING,
                SandboxState.SYNCING,
                SandboxState.DESTROYING,
                SandboxState.LOST,
            ]
        )
        for record in records:
            if record.lease_id == lease_id:
                return record
        raise LeaseNotFoundError(f"lease {lease_id} was not found")

    async def status(self, sandbox_id: str) -> SandboxRecord:
        record = await self._repository.get(sandbox_id)
        if record is None:
            raise LeaseNotFoundError(f"sandbox {sandbox_id} was not found")
        return record

    async def _destroy_record(self, record: SandboxRecord, reason: str) -> None:
        record.state = SandboxState.DESTROYING
        record.updated_at = utc_now()
        await self._repository.save(record)
        try:
            await self._provider.destroy(record.ref, reason)
        except Exception:
            record.state = SandboxState.LOST
            record.updated_at = utc_now()
            await self._repository.save(record)
            raise
        record.state = SandboxState.DESTROYED
        record.lease_id = None
        record.updated_at = utc_now()
        await self._repository.save(record)
