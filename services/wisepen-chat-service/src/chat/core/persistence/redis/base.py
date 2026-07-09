from __future__ import annotations

from redis.asyncio import Redis


class RedisRepository:
    """Redis 仓储基类，统一持有共享 async client。"""

    __slots__ = ("_redis",)

    def __init__(
        self,
        *,
        redis_client: Redis,
    ) -> None:
        self._redis = redis_client
