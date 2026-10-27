"""缓存成功的发现论文和显式请求范围。"""

import orjson
from pydantic import TypeAdapter
from redis.asyncio import Redis

from scholar.domain.interfaces.paper_discoverer import PaperDiscovery

_DISCOVERIES = TypeAdapter(PaperDiscovery)


class RedisDiscoveryCacheRepository:
    def __init__(self, redis: Redis, namespace: str, ttl: int = 900):
        self.redis = redis
        self.namespace = namespace
        self.ttl = ttl

    async def get(self, key: str) -> PaperDiscovery | None:
        value = await self.redis.get(f"scholar:discovery:{self.namespace}:{key}")
        return None if value is None else _DISCOVERIES.validate_json(value)

    async def put(self, key: str, discovery: PaperDiscovery) -> None:
        await self.redis.set(
            f"scholar:discovery:{self.namespace}:{key}",
            orjson.dumps(discovery),
            ex=self.ttl,
        )
