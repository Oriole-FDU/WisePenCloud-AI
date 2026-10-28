"""以规范 DOI 共享 Unpaywall 正负结果，不依赖索引或用户凭证。"""

import orjson
from redis.asyncio import Redis


class RedisPdfCache:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def get_many(self, dois: list[str]) -> dict[str, str | None]:
        values = await self.redis.mget([f"scholar:pdf:unpaywall:v1:{doi}" for doi in dois])
        return {
            doi: orjson.loads(value)
            for doi, value in zip(dois, values, strict=True)
            if value is not None
        }

    async def put_many(self, entries: list[tuple[str, str | None, int]]) -> None:
        async with self.redis.pipeline(transaction=True) as pipeline:
            for doi, url, ttl in entries:
                await pipeline.set(f"scholar:pdf:unpaywall:v1:{doi}", orjson.dumps(url), ex=ttl)
            await pipeline.execute()
