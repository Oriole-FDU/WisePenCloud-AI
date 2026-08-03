from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sandbox.domain.entities import SandboxRecord, TurnLeaseRecord


class LeaseManager(Protocol):
    async def find_lease(self, lease_id: str) -> SandboxRecord: ...

    async def get_turn_lease(self, lease_id: str) -> TurnLeaseRecord: ...

    async def close_lease(self, lease_id: str, fencing_token: int) -> SandboxRecord: ...

    async def validate_lease(
        self,
        lease_id: str,
        user_id: str,
        session_id: str,
        fencing_token: int,
        *,
        now: datetime | None = None,
    ) -> SandboxRecord: ...

    async def finish_release(
        self, lease_id: str, idle_ttl_seconds: int, *, error: str | None = None
    ) -> SandboxRecord: ...

    async def expired_turn_leases(self, now: datetime | None = None) -> list[TurnLeaseRecord]: ...

    async def find_turn_request(self, request_id: str) -> TurnLeaseRecord | None: ...

    async def active_turn_for_session(self, user_id: str, session_id: str) -> TurnLeaseRecord | None: ...

    async def active_turns_for_sandbox(self, sandbox_id: str) -> list[TurnLeaseRecord]: ...