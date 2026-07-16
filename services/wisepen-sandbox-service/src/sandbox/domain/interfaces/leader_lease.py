from __future__ import annotations

from typing import Protocol


class LeaderLease(Protocol):
    async def acquire(self, key: str, owner: str, ttl_seconds: float) -> bool:
        ...

    async def release(self, key: str, owner: str) -> None:
        ...
