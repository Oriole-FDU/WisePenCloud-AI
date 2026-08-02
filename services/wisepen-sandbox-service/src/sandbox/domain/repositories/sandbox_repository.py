from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol

from sandbox.domain.entities import (
    LeaseRecord,
    PoolSnapshot,
    SandboxRecord,
    SandboxState,
    TurnLeaseRecord,
)
from sandbox.domain.interfaces.metrics import MetricsPort



class SandboxRepository(Protocol):
    @property
    def metrics(self) -> MetricsPort: ...

    async def save(self, record: SandboxRecord) -> None: ...
    async def get(self, sandbox_id: str) -> SandboxRecord | None: ...
    async def records_in(self, states: Iterable[SandboxState]) -> list[SandboxRecord]: ...
    async def snapshot(self, *, min_ready: int = 0, target_ready: int = 0) -> PoolSnapshot: ...
    async def transition(
        self, sandbox_id: str, expected: SandboxState, state: SandboxState, *, error: str | None = None
    ) -> SandboxRecord: ...

    async def checkout_ready(
        self,
        request_id: str,
        user_id: str,
        session_id: str,
        lease_ttl_seconds: int,
        user_idle_ttl_seconds: int = 600,
        max_user_bindings: int = 20,
    ) -> tuple[SandboxRecord, LeaseRecord]: ...

    async def find_turn_request(self, request_id: str) -> TurnLeaseRecord | None: ...
    async def active_turn_for_session(self, user_id: str, session_id: str) -> TurnLeaseRecord | None: ...
    async def active_turns_for_sandbox(self, sandbox_id: str) -> list[TurnLeaseRecord]: ...

    async def prepare_ready(self, record: SandboxRecord, readiness_token: str) -> int: ...
    async def return_ready(
        self, sandbox_id: str, health_token: str, expected_generation: int
    ) -> SandboxRecord: ...

    async def records_older_than(self, state: SandboxState, cutoff: datetime) -> list[SandboxRecord]: ...
