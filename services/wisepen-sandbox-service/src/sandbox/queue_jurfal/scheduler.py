from __future__ import annotations

import asyncio
from time import monotonic

from sandbox.errors import (
    LeaseNotFoundError,
    SandboxDomainError,
    SandboxUnavailableError,
    WorkspaceSyncError,
)
from sandbox.queue_jurfal.models import (
    DestroyReason,
    ExecutionRequest,
    ExecutionResult,
    SandboxLease,
    SandboxRecord,
    SandboxRef,
    SandboxState,
    utc_now,
)
from sandbox.queue_jurfal.ports import SandboxProvider, WorkspaceStore
from sandbox.queue_jurfal.pool import SandboxPool
from sandbox.queue_jurfal.repository import InMemorySandboxRepository


class SandboxScheduler:
    def __init__(
        self,
        pool: SandboxPool,
        repository: InMemorySandboxRepository,
        provider: SandboxProvider,
        workspace_store: WorkspaceStore,
        destroy_timeout_seconds: float = 30.0,
        destroy_max_retries: int = 3,
        destroy_backoff_seconds: float = 0.1,
    ) -> None:
        self._pool = pool
        self._repository = repository
        self._provider = provider
        self._workspace_store = workspace_store
        self._lifecycle_lock = asyncio.Lock()
        self._released_leases: set[str] = set()
        self._destroy_timeout = destroy_timeout_seconds
        self._destroy_max_retries = max(1, destroy_max_retries)
        self._destroy_backoff = destroy_backoff_seconds

    async def allocate(
        self, request_id: str, tenant_id: str, workspace_id: str
    ) -> SandboxLease:
        if not request_id or not tenant_id or not workspace_id:
            raise SandboxDomainError("request, tenant and workspace are required")
        async with self._lifecycle_lock:
            record, lease = await self._pool.checkout(request_id, tenant_id, workspace_id)
            if record.state == SandboxState.RUNNING:
                return lease
            try:
                workspace = await self._workspace_store.snapshot(tenant_id, workspace_id)
                await self._provider.prepare_workspace(record.ref, workspace)
                endpoint = await self._provider.activate(record.ref, lease)
                record.ref = SandboxRef(
                    sandbox_id=record.ref.sandbox_id,
                    provider_id=record.ref.provider_id,
                    endpoint=endpoint,
                    metadata=record.ref.metadata,
                )
                await self._repository.save(record)
                await self._repository.transition(
                    record.ref.sandbox_id,
                    SandboxState.ALLOCATED,
                    SandboxState.RUNNING,
                )
                record = await self._repository.get(record.ref.sandbox_id)
                if record is None:
                    raise SandboxUnavailableError("sandbox record lost after allocation")
                return self._lease(record)
            except Exception as exc:
                await self._destroy_record(record, DestroyReason.ALLOCATION_FAILED)
                if isinstance(exc, SandboxDomainError):
                    raise
                raise SandboxUnavailableError("sandbox allocation failed") from exc

    async def execute(
        self, lease_id: str, request: ExecutionRequest
    ) -> ExecutionResult:
        async with self._lifecycle_lock:
            record = await self._repository.validate_lease(
                lease_id,
                request.tenant_id,
                request.workspace_id,
                request.fencing_token,
            )
            if record.state != SandboxState.RUNNING:
                raise SandboxUnavailableError("sandbox lease is not running")
            try:
                return await self._provider.forward(record.ref, request)
            except SandboxDomainError:
                raise
            except Exception as exc:
                raise SandboxUnavailableError("sandbox execution failed") from exc

    async def release(self, lease_id: str, fencing_token: int) -> None:
        async with self._lifecycle_lock:
            if lease_id in self._released_leases:
                return
            record = await self._repository.find_lease(lease_id)
            if record.state in (SandboxState.DESTROYED, SandboxState.LOST):
                self._released_leases.add(lease_id)
                return
            record = await self._repository.close_lease(lease_id, fencing_token)
            if record.state in (SandboxState.DESTROYING, SandboxState.DESTROYED,
                                SandboxState.SYNCING, SandboxState.LOST):
                return
            commit_error: Exception | None = None
            try:
                snapshot = await self._provider.export_workspace(
                    record.ref, record.tenant_id or "", record.workspace_id or ""
                )
                await self._workspace_store.commit(
                    snapshot,
                    record.lease_id or lease_id,
                    record.fencing_token,
                )
                self._repository.metrics.increment("workspace_commit_successes")
            except Exception as exc:
                self._repository.metrics.increment("workspace_commit_failures")
                commit_error = WorkspaceSyncError("workspace commit failed")
                commit_error.__cause__ = exc
            finally:
                await self._destroy_record(record, DestroyReason.LEASE_RELEASED)
                self._released_leases.add(lease_id)
            if commit_error:
                raise commit_error

    async def recover_expired(self) -> int:
        recovered = 0
        async with self._lifecycle_lock:
            for record in await self._repository.expired_leases():
                if not record.lease_id:
                    continue
                lease_id = record.lease_id
                try:
                    await self._repository.close_lease(lease_id, record.fencing_token)
                    await self._destroy_record(record, DestroyReason.LEASE_EXPIRED)
                    self._released_leases.add(lease_id)
                    self._repository.metrics.increment("expired_lease_recoveries")
                    recovered += 1
                except Exception:
                    pass
        return recovered

    async def status(self, sandbox_id: str) -> SandboxRecord:
        record = await self._repository.get(sandbox_id)
        if record is None:
            raise LeaseNotFoundError(f"sandbox {sandbox_id} was not found")
        return record

    def _lease(self, record: SandboxRecord) -> SandboxLease:
        return SandboxLease(
            lease_id=record.lease_id or "",
            request_id=record.request_id or "",
            sandbox_id=record.ref.sandbox_id,
            tenant_id=record.tenant_id or "",
            workspace_id=record.workspace_id or "",
            expires_at=record.lease_expires_at or utc_now(),
            fencing_token=record.fencing_token,
            endpoint=record.ref.endpoint,
        )

    async def _destroy_record(
        self, record: SandboxRecord, reason: DestroyReason
    ) -> None:
        if record.state == SandboxState.DESTROYED:
            return
        if record.state != SandboxState.DESTROYING:
            await self._repository.transition(
                record.ref.sandbox_id,
                record.state,
                SandboxState.DESTROYING,
            )
        last_error: Exception | None = None
        for attempt in range(self._destroy_max_retries):
            self._repository.metrics.increment("destroy_attempts")
            started = monotonic()
            try:
                await asyncio.wait_for(
                    self._provider.destroy(record.ref, reason.value),
                    timeout=self._destroy_timeout,
                )
                self._repository.metrics.observe_ms(
                    "destroy", (monotonic() - started) * 1000
                )
                self._repository.metrics.increment("destroy_successes")
                last_error = None
                break
            except Exception as exc:
                self._repository.metrics.observe_ms(
                    "destroy", (monotonic() - started) * 1000
                )
                last_error = exc
                if attempt + 1 < self._destroy_max_retries:
                    await asyncio.sleep(self._destroy_backoff * (2**attempt))
        if last_error is not None:
            await self._repository.transition(
                record.ref.sandbox_id,
                SandboxState.DESTROYING,
                SandboxState.LOST,
                error=str(last_error)[:200],
            )
            self._repository.metrics.increment("destroy_failures")
            raise SandboxUnavailableError("sandbox destroy failed") from last_error
        await self._repository.transition(
            record.ref.sandbox_id,
            SandboxState.DESTROYING,
            SandboxState.DESTROYED,
        )
        await self._repository.clear_lease(record)
