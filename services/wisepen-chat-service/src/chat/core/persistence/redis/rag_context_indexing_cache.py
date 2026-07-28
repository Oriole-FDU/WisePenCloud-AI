from __future__ import annotations

from collections.abc import Mapping, Sequence

from redis.asyncio import Redis

from chat.application.rag.repositories import RagContextIndexingCache

from .base import RedisRepository

_KEY_PREFIX = "rag:context-indexing:"


class RedisRagContextIndexingCache(RedisRepository, RagContextIndexingCache):
    __slots__ = ("_ttl_seconds",)

    def __init__(self, *, redis_client: Redis, ttl_seconds: int) -> None:
        super().__init__(redis_client=redis_client)
        self._ttl_seconds = ttl_seconds

    async def get_many(self, keys: Sequence[str]) -> dict[str, str]:
        unique_keys = tuple(dict.fromkeys(keys))
        if not unique_keys:
            return {}
        values = await self._redis.mget([f"{_KEY_PREFIX}{key}" for key in unique_keys])
        return {
            key: value.decode() if isinstance(value, bytes) else value
            for key, value in zip(unique_keys, values, strict=True)
            if isinstance(value, (bytes, str))
        }

    async def set_many(self, values: Mapping[str, str]) -> None:
        if not values:
            return
        async with self._redis.pipeline(transaction=False) as pipe:
            for key, value in values.items():
                pipe.set(f"{_KEY_PREFIX}{key}", value, ex=self._ttl_seconds)
            await pipe.execute()
