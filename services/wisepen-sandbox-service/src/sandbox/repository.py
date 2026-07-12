from __future__ import annotations

import asyncio
from typing import Iterable

from sandbox.models import SandboxRecord, SandboxState, utc_now


class InMemorySandboxRepository:
    def __init__(self) -> None:
        self._records: dict[str, SandboxRecord] = {}
        self._request_leases: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def save(self, record: SandboxRecord) -> None:
        async with self._lock:
            self._records[record.ref.sandbox_id] = record

    async def get(self, sandbox_id: str) -> SandboxRecord | None:
        async with self._lock:
            return self._records.get(sandbox_id)

    async def find_request(self, request_id: str) -> SandboxRecord | None:
        async with self._lock:
            sandbox_id = self._request_leases.get(request_id)
            return self._records.get(sandbox_id) if sandbox_id else None

    async def bind_request(self, request_id: str, sandbox_id: str) -> None:
        async with self._lock:
            self._request_leases[request_id] = sandbox_id

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

    async def transition(
        self, sandbox_id: str, expected: SandboxState, state: SandboxState
    ) -> SandboxRecord:
        async with self._lock:
            record = self._records[sandbox_id]
            if record.state != expected:
                raise ValueError(
                    f"expected {expected.value}, got {record.state.value}"
                )
            record.state = state
            record.state_version += 1
            record.updated_at = utc_now()
            return record
