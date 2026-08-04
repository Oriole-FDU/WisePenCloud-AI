from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol

from sandbox_v1.domain.entities import (
    PoolSnapshot,
    SandboxRecord,
    SandboxState,
)
from sandbox_v1.domain.interfaces.metrics import MetricsPort


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
        user_id: str,
        max_user_bindings: int = 20,
    ) -> SandboxRecord: ...

    async def records_older_than(self, state: SandboxState, cutoff: datetime) -> list[SandboxRecord]: ...
