from __future__ import annotations

import redis.asyncio as redis


class RedisRepository:
    """Redis 仓储基类，统一 async client 生命周期。"""

    __slots__ = ("_redis",)

    def __init__(self, *, redis_url: str) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)

    async def aclose(self) -> None:
        await self._redis.aclose()
