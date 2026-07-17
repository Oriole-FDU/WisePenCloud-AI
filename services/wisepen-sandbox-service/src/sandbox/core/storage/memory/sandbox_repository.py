from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Iterable

from common.core.exceptions import ServiceException

from sandbox.domain.entities import (
    LeaseRecord,
    SandboxRecord,
    SandboxState,
    PoolSnapshot,
    utc_now,
)
from sandbox.core.observability.metrics import MetricsCollector
from sandbox.domain.interfaces.metrics import MetricsPort
from sandbox.domain.error_codes import SandboxErrorCode


_ALLOWED_TRANSITIONS: dict[SandboxState, frozenset[SandboxState]] = {
    SandboxState.CREATING: frozenset({SandboxState.WARMING, SandboxState.DESTROYING}),
    SandboxState.WARMING: frozenset({SandboxState.READY, SandboxState.DESTROYING}),
    SandboxState.READY: frozenset({SandboxState.ALLOCATED, SandboxState.DESTROYING}),
    SandboxState.ALLOCATED: frozenset({SandboxState.RUNNING, SandboxState.DESTROYING}),
    SandboxState.RUNNING: frozenset({SandboxState.SYNCING, SandboxState.DESTROYING}),
    SandboxState.SYNCING: frozenset({SandboxState.DESTROYING}),
    SandboxState.DESTROYING: frozenset({SandboxState.DESTROYED, SandboxState.LOST}),
    SandboxState.DESTROYED: frozenset(),
    SandboxState.LOST: frozenset(),
}


class MemorySandboxRepository:
    """Atomic in-process repository; external stores can implement the same port."""

    def __init__(self, metrics: MetricsPort | None = None) -> None:
        self._records: dict[str, SandboxRecord] = {}
        self._leases: dict[str, str] = {}
        self._requests: dict[str, str] = {}
        self._generation = 0
        self._empty_checkouts = 0
        self._next_fencing_token = 0
        self._lock = asyncio.Lock()
        self.metrics = metrics or MetricsCollector()

    async def save(self, record: SandboxRecord) -> None:
        async with self._lock:
            self._records[record.ref.sandbox_id] = record
            self._generation += 1

    async def get(self, sandbox_id: str) -> SandboxRecord | None:
        async with self._lock:
            return self._records.get(sandbox_id)

    async def find_request(self, request_id: str) -> SandboxRecord | None:
        async with self._lock:
            sandbox_id = self._requests.get(request_id)
            return self._records.get(sandbox_id) if sandbox_id else None

    async def bind_request(self, request_id: str, sandbox_id: str) -> None:
        async with self._lock:
            existing = self._requests.get(request_id)
            if existing and existing != sandbox_id:
                raise ServiceException(
                    SandboxErrorCode.REQUEST_CONFLICT,
                    "request_id is already bound to another sandbox",
                )
            self._requests[request_id] = sandbox_id
            self._generation += 1

    async def unbind_request(self, request_id: str) -> None:
        async with self._lock:
            self._requests.pop(request_id, None)
            self._generation += 1

    async def records_in(self, states: Iterable[SandboxState]) -> list[SandboxRecord]:
        wanted = set(states)
        async with self._lock:
            return [record for record in self._records.values() if record.state in wanted]

    async def counts(self) -> dict[SandboxState, int]:
        async with self._lock:
            counts = {state: 0 for state in SandboxState}
            for record in self._records.values():
                counts[record.state] += 1
            return counts

    async def snapshot(self, *, min_ready: int = 0, target_ready: int = 0) -> PoolSnapshot:
        async with self._lock:
            counts = {state: 0 for state in SandboxState}
            for record in self._records.values():
                counts[record.state] += 1
            ready = counts[SandboxState.READY]
            now = utc_now()
            self.metrics.set_value(
                "zombie_leases",
                sum(
                    1
                    for record in self._records.values()
                    if record.state in (SandboxState.ALLOCATED, SandboxState.RUNNING)
                    and record.lease_expires_at is not None
                    and record.lease_expires_at <= now
                ),
            )
            return PoolSnapshot(
                self._generation,
                counts,
                self._empty_checkouts,
                self.metrics.snapshot(ready, min_ready, target_ready),
                min_ready,
                target_ready,
            )

    async def transition(
        self,
        sandbox_id: str,
        expected: SandboxState,
        state: SandboxState,
        *,
        error: str | None = None,
    ) -> SandboxRecord:
        async with self._lock:
            record = self._records.get(sandbox_id)
            if record is None:
                raise ServiceException(
                    SandboxErrorCode.LEASE_NOT_FOUND,
                    f"sandbox {sandbox_id} was not found",
                )
            if record.state != expected:
                raise ServiceException(
                    SandboxErrorCode.INVALID_STATE_TRANSITION,
                    f"expected {expected.value}, got {record.state.value}"
                )
            if state not in _ALLOWED_TRANSITIONS[expected]:
                raise ServiceException(
                    SandboxErrorCode.INVALID_STATE_TRANSITION,
                    f"cannot transition {expected.value} to {state.value}"
                )
            record.state = state
            record.state_version += 1
            record.updated_at = utc_now()
            record.last_error = error
            self._generation += 1
            return record

    async def checkout_ready(
        self,
        request_id: str,
        tenant_id: str,
        workspace_id: str,
        lease_ttl_seconds: int,
    ) -> tuple[SandboxRecord, LeaseRecord]:
        async with self._lock:
            existing_id = self._requests.get(request_id)
            if existing_id:
                existing = self._records[existing_id]
                if existing.tenant_id != tenant_id or existing.workspace_id != workspace_id:
                    raise ServiceException(
                        SandboxErrorCode.REQUEST_CONFLICT,
                        "request_id context does not match existing lease",
                    )
                return existing, self._lease_for(existing)

            ready = next(
                (record for record in self._records.values() if record.state == SandboxState.READY),
                None,
            )
            if ready is None:
                self._empty_checkouts += 1
                self.metrics.increment("pool_empty_checkouts")
                raise ServiceException(
                    SandboxErrorCode.POOL_EMPTY,
                    "no ready sandbox is available",
                )
            self._next_fencing_token += 1
            now = utc_now()
            ready.state = SandboxState.ALLOCATED
            ready.state_version += 1
            ready.updated_at = now
            ready.lease_id = f"lease_{self._next_fencing_token}"
            ready.request_id = request_id
            ready.tenant_id = tenant_id
            ready.workspace_id = workspace_id
            ready.lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
            ready.fencing_token = self._next_fencing_token
            self.metrics.lease_started(tenant_id)
            self.metrics.increment("allocate_successes")
            self._leases[ready.lease_id] = ready.ref.sandbox_id
            self._requests[request_id] = ready.ref.sandbox_id
            self._generation += 1
            return ready, self._lease_for(ready)

    def _lease_for(self, record: SandboxRecord) -> LeaseRecord:
        return LeaseRecord(
            lease_id=record.lease_id or "",
            request_id=record.request_id or "",
            sandbox_id=record.ref.sandbox_id,
            tenant_id=record.tenant_id or "",
            workspace_id=record.workspace_id or "",
            expires_at=record.lease_expires_at or utc_now(),
            fencing_token=record.fencing_token,
            endpoint=record.ref.endpoint,
        )

    async def find_lease(self, lease_id: str) -> SandboxRecord:
        async with self._lock:
            sandbox_id = self._leases.get(lease_id)
            record = self._records.get(sandbox_id) if sandbox_id else None
            if record is None:
                raise ServiceException(
                    SandboxErrorCode.LEASE_NOT_FOUND,
                    f"lease {lease_id} was not found",
                )
            return record

    async def close_lease(self, lease_id: str, fencing_token: int) -> SandboxRecord:
        async with self._lock:
            sandbox_id = self._leases.get(lease_id)
            record = self._records.get(sandbox_id) if sandbox_id else None
            if record is None:
                raise ServiceException(
                    SandboxErrorCode.LEASE_NOT_FOUND,
                    f"lease {lease_id} was not found",
                )
            if record.fencing_token != fencing_token:
                raise ServiceException(
                    SandboxErrorCode.FENCING_REJECTED,
                    "lease fencing token is stale",
                )
            if record.state == SandboxState.DESTROYED:
                return record
            if record.state in (SandboxState.SYNCING, SandboxState.DESTROYING, SandboxState.LOST):
                return record
            if record.state not in (SandboxState.ALLOCATED, SandboxState.RUNNING):
                raise ServiceException(
                    SandboxErrorCode.INVALID_STATE_TRANSITION,
                    f"cannot release {record.state.value} sandbox",
                )
            record.state = SandboxState.SYNCING
            record.state_version += 1
            record.updated_at = utc_now()
            self._generation += 1
            return record

    async def validate_lease(
        self,
        lease_id: str,
        tenant_id: str,
        workspace_id: str,
        fencing_token: int,
        *,
        now: datetime | None = None,
    ) -> SandboxRecord:
        record = await self.find_lease(lease_id)
        if record.tenant_id != tenant_id or record.workspace_id != workspace_id:
            raise ServiceException(
                SandboxErrorCode.FENCING_REJECTED,
                "lease context does not match",
            )
        if record.fencing_token != fencing_token:
            raise ServiceException(
                SandboxErrorCode.FENCING_REJECTED,
                "lease fencing token is stale",
            )
        if record.lease_expires_at and record.lease_expires_at <= (now or utc_now()):
            raise ServiceException(
                SandboxErrorCode.LEASE_EXPIRED,
                "sandbox lease has expired",
            )
        return record

    async def clear_lease(self, record: SandboxRecord) -> None:
        async with self._lock:
            tenant_id = record.tenant_id
            if record.lease_id:
                self._leases.pop(record.lease_id, None)
            if record.request_id:
                self._requests.pop(record.request_id, None)
            record.lease_id = None
            record.request_id = None
            record.tenant_id = None
            record.workspace_id = None
            record.lease_expires_at = None
            if tenant_id:
                self.metrics.lease_finished(tenant_id)
            self._generation += 1

    async def prepare_ready(
        self, record: SandboxRecord, readiness_token: str
    ) -> int:
        async with self._lock:
            current = self._records.get(record.ref.sandbox_id)
            if current is None or current.state != SandboxState.WARMING:
                raise ServiceException(
                    SandboxErrorCode.INVALID_STATE_TRANSITION,
                    "only warming sandboxes can prepare readiness"
                )
            record.readiness_token = readiness_token
            self._records[record.ref.sandbox_id] = record
            self._generation += 1
            return self._generation

    async def return_ready(
        self,
        sandbox_id: str,
        health_token: str,
        expected_generation: int,
    ) -> SandboxRecord:
        async with self._lock:
            record = self._records.get(sandbox_id)
            if record is None:
                raise ServiceException(
                    SandboxErrorCode.LEASE_NOT_FOUND,
                    f"sandbox {sandbox_id} was not found",
                )
            if self._generation != expected_generation:
                raise ServiceException(
                    SandboxErrorCode.FENCING_REJECTED,
                    "pool generation is stale",
                )
            if record.state != SandboxState.WARMING:
                raise ServiceException(
                    SandboxErrorCode.INVALID_STATE_TRANSITION,
                    "only warming sandboxes can return ready",
                )
            if any((record.lease_id, record.request_id, record.tenant_id, record.workspace_id)):
                raise ServiceException(
                    SandboxErrorCode.FENCING_REJECTED,
                    "sandbox still has an active lease",
                )
            if not record.readiness_token or record.readiness_token != health_token:
                raise ServiceException(
                    SandboxErrorCode.FENCING_REJECTED,
                    "sandbox health token is invalid",
                )
            record.state = SandboxState.READY
            record.readiness_token = None
            record.state_version += 1
            record.updated_at = utc_now()
            self._generation += 1
            self.metrics.increment("ready_returns")
            return record

    async def expired_leases(self, now: datetime | None = None) -> list[SandboxRecord]:
        current = now or utc_now()
        async with self._lock:
            return [
                record
                for record in self._records.values()
                if record.state in (SandboxState.ALLOCATED, SandboxState.RUNNING)
                and record.lease_expires_at is not None
                and record.lease_expires_at <= current
            ]

    async def records_older_than(
        self, state: SandboxState, cutoff: datetime
    ) -> list[SandboxRecord]:
        async with self._lock:
            return [
                record
                for record in self._records.values()
                if record.state == state and record.updated_at <= cutoff
            ]
