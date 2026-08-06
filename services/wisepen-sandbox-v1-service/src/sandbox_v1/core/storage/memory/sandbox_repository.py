from __future__ import annotations

import uuid
from datetime import datetime
from typing import Iterable

from common.core.exceptions import ServiceException

from sandbox_v1.core.observability.metrics import MetricsCollector
from sandbox_v1.core.storage.memory.state import _RepositoryState
from sandbox_v1.domain.entities import (
    PoolSnapshot,
    SandboxRecord,
    SandboxState,
    UserSandboxBindingRecord,
    utc_now,
)
from sandbox_v1.domain.error_codes import SandboxErrorCode
from sandbox_v1.domain.interfaces.metrics import MetricsPort


_ALLOWED_TRANSITIONS: dict[SandboxState, frozenset[SandboxState]] = {
    SandboxState.CREATING: frozenset({SandboxState.WARMING, SandboxState.DESTROYING}),
    SandboxState.WARMING: frozenset({SandboxState.READY, SandboxState.DESTROYING}),
    SandboxState.READY: frozenset({SandboxState.USER_ACTIVE, SandboxState.DESTROYING}),
    SandboxState.USER_ACTIVE: frozenset(
        {SandboxState.RETIRING, SandboxState.DESTROYING}
    ),
    SandboxState.RETIRING: frozenset({SandboxState.DESTROYING}),
    SandboxState.DESTROYING: frozenset({SandboxState.DESTROYED, SandboxState.LOST}),
    SandboxState.DESTROYED: frozenset(),
    SandboxState.LOST: frozenset(),
}


class MemorySandboxRepository:
    """Atomic in-memory authority for pool records and user bindings."""

    def __init__(self, metrics: MetricsPort | None = None) -> None:
        state = _RepositoryState(metrics=metrics or MetricsCollector())
        self._state = state

    @property
    def metrics(self) -> MetricsPort:
        return self._state.metrics

    def _new_binding(
        self, sandbox_id: str, user_id: str
    ) -> UserSandboxBindingRecord:
        binding = UserSandboxBindingRecord(
            user_binding_id=f"user_{uuid.uuid4().hex}",
            sandbox_id=sandbox_id,
            user_id=user_id,
        )
        self._state.user_bindings[user_id] = binding
        self._state.sandbox_bindings[sandbox_id] = user_id
        self._state.metrics.increment("user_bindings_created")
        return binding

    async def save(self, record: SandboxRecord) -> None:
        async with self._state.lock:
            self._state.records[record.ref.sandbox_id] = record
            self._state.generation += 1

    async def get(self, sandbox_id: str) -> SandboxRecord | None:
        async with self._state.lock:
            return self._state.records.get(sandbox_id)

    async def records_in(
        self, states: Iterable[SandboxState]
    ) -> list[SandboxRecord]:
        wanted = set(states)
        async with self._state.lock:
            return [
                record
                for record in self._state.records.values()
                if record.state in wanted
            ]

    async def snapshot(
        self, *, min_ready: int = 0, target_ready: int = 0
    ) -> PoolSnapshot:
        async with self._state.lock:
            counts = {state: 0 for state in SandboxState}
            for record in self._state.records.values():
                counts[record.state] += 1

            ready = counts[SandboxState.READY]
            self._state.metrics.set_value(
                "active_user_bindings", counts[SandboxState.USER_ACTIVE]
            )
            return PoolSnapshot(
                generation=self._state.generation,
                counts=counts,
                empty_checkouts=self._state.empty_checkouts,
                metrics=self._state.metrics.snapshot(
                    ready, min_ready, target_ready
                ),
                min_ready=min_ready,
                target_ready=target_ready,
            )

    async def transition(
        self,
        sandbox_id: str,
        expected: SandboxState,
        state: SandboxState,
        *,
        error: str | None = None,
    ) -> SandboxRecord:
        async with self._state.lock:
            record = self._state.records.get(sandbox_id)
            if record is None:
                raise ServiceException(
                    SandboxErrorCode.SANDBOX_UNAVAILABLE,
                    f"sandbox {sandbox_id} does not exist",
                )
            if record.state != expected or state not in _ALLOWED_TRANSITIONS[expected]:
                raise ServiceException(
                    SandboxErrorCode.INVALID_STATE_TRANSITION,
                    f"cannot transition {record.state.value} to {state.value}",
                )
            record.state = state
            record.state_version += 1
            record.updated_at = utc_now()
            record.last_error = error
            self._state.generation += 1
            return record

    async def checkout_ready(
        self,
        user_id: str,
        max_user_bindings: int = 20,
    ) -> SandboxRecord:
        """Assign a READY container, or reuse the user's existing binding."""
        async with self._state.lock:
            binding = self._state.user_bindings.get(user_id)
            if binding is None:
                if len(self._state.user_bindings) >= max_user_bindings:
                    raise ServiceException(
                        SandboxErrorCode.USER_SANDBOX_CAPACITY,
                        "user sandbox capacity has been reached",
                    )
                record = next(
                    (
                        item
                        for item in self._state.records.values()
                        if item.state == SandboxState.READY
                    ),
                    None,
                )
                if record is None:
                    self._state.empty_checkouts += 1
                    self._state.metrics.increment("pool_empty_checkouts")
                    raise ServiceException(
                        SandboxErrorCode.POOL_EMPTY,
                        "sandbox pool has no READY container",
                    )
                # Consumption is deliberately just assignment; the pool core does
                # not keep per-operation ownership or request idempotency state.
                record.state = SandboxState.USER_ACTIVE
                record.state_version += 1
                binding = self._new_binding(record.ref.sandbox_id, user_id)
                record.owner_user_id = user_id
                record.user_binding_id = binding.user_binding_id
            else:
                record = self._state.records.get(binding.sandbox_id)
                if record is None:
                    raise ServiceException(
                        SandboxErrorCode.SANDBOX_UNAVAILABLE,
                        "user binding points to a missing container",
                    )
                if record.state not in {
                    SandboxState.USER_ACTIVE,
                }:
                    raise ServiceException(
                        SandboxErrorCode.SANDBOX_UNAVAILABLE,
                        "user container is not available",
                    )
                binding.reuse_count += 1
                record.reuse_count = binding.reuse_count
                self._state.metrics.increment("user_container_reuse_hits")

            now = utc_now()
            record.updated_at = now
            binding.updated_at = now
            binding.last_active_at = now
            self._state.generation += 1
            return record

    async def records_older_than(
        self, state: SandboxState, cutoff: datetime
    ) -> list[SandboxRecord]:
        async with self._state.lock:
            return [
                record
                for record in self._state.records.values()
                if record.state == state and record.updated_at <= cutoff
            ]
