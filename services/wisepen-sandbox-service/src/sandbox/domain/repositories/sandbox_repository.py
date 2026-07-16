from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol

from sandbox.domain.entities import (
    LeaseRecord,
    PoolSnapshot,
    SandboxRecord,
    SandboxState,
)
from sandbox.domain.interfaces.metrics import MetricsPort


class SandboxRepository(Protocol):
    @property
    def metrics(self) -> MetricsPort:
        ...

    async def save(self, record: SandboxRecord) -> None:
        ...

    async def get(self, sandbox_id: str) -> SandboxRecord | None:
        ...

    async def records_in(self, states: Iterable[SandboxState]) -> list[SandboxRecord]:
        ...

    async def snapshot(self, *, min_ready: int = 0, target_ready: int = 0) -> PoolSnapshot:
        ...

    async def transition(
        self,
        sandbox_id: str,
        expected: SandboxState,
        state: SandboxState,
        *,
        error: str | None = None,
    ) -> SandboxRecord:
        ...

    async def checkout_ready(
        self,
        request_id: str,
        tenant_id: str,
        workspace_id: str,
        lease_ttl_seconds: int,
    ) -> tuple[SandboxRecord, LeaseRecord]:
        ...

    async def find_lease(self, lease_id: str) -> SandboxRecord:
        ...

    async def close_lease(self, lease_id: str, fencing_token: int) -> SandboxRecord:
        ...

    async def validate_lease(
        self,
        lease_id: str,
        tenant_id: str,
        workspace_id: str,
        fencing_token: int,
        *,
        now: datetime | None = None,
    ) -> SandboxRecord:
        ...

    async def clear_lease(self, record: SandboxRecord) -> None:
        ...

    async def prepare_ready(self, record: SandboxRecord, readiness_token: str) -> int:
        ...

    async def return_ready(
        self, sandbox_id: str, health_token: str, expected_generation: int
    ) -> SandboxRecord:
        ...

    async def expired_leases(self, now: datetime | None = None) -> list[SandboxRecord]:
        ...

    async def records_older_than(
        self, state: SandboxState, cutoff: datetime
    ) -> list[SandboxRecord]:
        ...
