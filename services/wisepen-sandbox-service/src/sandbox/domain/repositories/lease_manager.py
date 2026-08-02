from __future__ import annotations

from datetime import datetime

from sandbox.domain.entities import SandboxRecord, TurnLeaseRecord


class LeaseManager:
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