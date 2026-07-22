from __future__ import annotations

import asyncio
from time import monotonic


class MemoryLeaderLease:
    """进程内选主租约。

    该实现只适合同进程测试和单实例运行；多副本部署时需要替换成 Redis/数据库锁。
    """

    def __init__(self) -> None:
        self._leases: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str, owner: str, ttl_seconds: float) -> bool:
        async with self._lock:
            current = self._leases.get(key)
            now = monotonic()
            # 未过期且 owner 不同，说明已有其它 Watcher 持有本轮 reconcile 权限。
            if current and current[1] > now and current[0] != owner:
                return False
            self._leases[key] = (owner, now + ttl_seconds)
            return True

    async def release(self, key: str, owner: str) -> None:
        async with self._lock:
            current = self._leases.get(key)
            if current and current[0] == owner:
                self._leases.pop(key, None)
