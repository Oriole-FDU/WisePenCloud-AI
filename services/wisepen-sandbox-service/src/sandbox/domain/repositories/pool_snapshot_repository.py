from __future__ import annotations

from typing import Protocol

from sandbox.domain.entities import PoolSnapshot


class PoolSnapshotRepository(Protocol):
    """PoolSnapshot 的 Redis 权威仓储端口"""

    async def save(self, snapshot: PoolSnapshot) -> None:
        """保存或覆盖当前池状态快照"""
        ...

    async def get(self) -> PoolSnapshot | None:
        """读取当前池状态快照"""
        ...