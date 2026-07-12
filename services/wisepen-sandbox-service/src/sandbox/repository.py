from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Iterable

from sandbox.errors import (
    FencingRejectedError,
    InvalidStateTransition,
    LeaseConflictError,
    LeaseNotFoundError,
    LeaseExpiredError,
    PoolEmptyError,
)
from sandbox.models import (
    LeaseRecord,
    SandboxRecord,
    SandboxState,
    PoolSnapshot,
    utc_now,
)


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


class InMemorySandboxRepository:
    """Atomic in-process repository; external stores can implement the same port."""

    def __init__(self) -> None:
        self._records: dict[str, SandboxRecord] = {}
        self._leases: dict[str, str] = {}
        self._requests: dict[str, str] = {}
        self._generation = 0
        self._empty_checkouts = 0
        self._next_fencing_token = 0
        self._lock = asyncio.Lock()

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
                raise LeaseConflictError("request_id is already bound to another sandbox")
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

    async def snapshot(self) -> PoolSnapshot:
        async with self._lock:
            counts = {state: 0 for state in SandboxState}
            for record in self._records.values():
                counts[record.state] += 1
            return PoolSnapshot(self._generation, counts, self._empty_checkouts)

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
                raise LeaseNotFoundError(f"sandbox {sandbox_id} was not found")
            if record.state != expected:
                raise InvalidStateTransition(
                    f"expected {expected.value}, got {record.state.value}"
                )
            if state not in _ALLOWED_TRANSITIONS[expected]:
                raise InvalidStateTransition(
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
                    raise LeaseConflictError("request_id context does not match existing lease")
                return existing, self._lease_for(existing)

            ready = next(
                (record for record in self._records.values() if record.state == SandboxState.READY),
                None,
            )
            if ready is None:
                self._empty_checkouts += 1
                raise PoolEmptyError("no ready sandbox is available")
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
                raise LeaseNotFoundError(f"lease {lease_id} was not found")
            return record

    async def close_lease(self, lease_id: str, fencing_token: int) -> SandboxRecord:
        async with self._lock:
            sandbox_id = self._leases.get(lease_id)
            record = self._records.get(sandbox_id) if sandbox_id else None
            if record is None:
                raise LeaseNotFoundError(f"lease {lease_id} was not found")
            if record.fencing_token != fencing_token:
                raise FencingRejectedError("lease fencing token is stale")
            if record.state == SandboxState.DESTROYED:
                return record
            if record.state in (SandboxState.SYNCING, SandboxState.DESTROYING, SandboxState.LOST):
                return record
            if record.state not in (SandboxState.ALLOCATED, SandboxState.RUNNING):
                raise InvalidStateTransition(f"cannot release {record.state.value} sandbox")
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
            raise FencingRejectedError("lease context does not match")
        if record.fencing_token != fencing_token:
            raise FencingRejectedError("lease fencing token is stale")
        if record.lease_expires_at and record.lease_expires_at <= (now or utc_now()):
            raise LeaseExpiredError("sandbox lease has expired")
        return record

    async def clear_lease(self, record: SandboxRecord) -> None:
        async with self._lock:
            if record.lease_id:
                self._leases.pop(record.lease_id, None)
            if record.request_id:
                self._requests.pop(record.request_id, None)
            record.lease_id = None
            record.request_id = None
            record.tenant_id = None
            record.workspace_id = None
            record.lease_expires_at = None
            self._generation += 1

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
