"""Redis 仅缓存规范论文事实，不持久化来源执行状态。"""

import orjson
from pydantic import TypeAdapter
from redis.asyncio import Redis

from scholar.core.storage.redis import CACHE_REVISION
from scholar.domain.models.paper import Paper
from scholar.domain.repositories.paper_cache import PaperCache
from scholar.core.providers.policies import paper_identifiers

_PAPERS = TypeAdapter(list[Paper])


class RedisQueryCacheRepository:
    def __init__(self, redis: Redis, namespace: str, ttl: int, paper_cache: PaperCache | None = None):
        self.redis, self.namespace, self.ttl = (
            redis,
            f"{CACHE_REVISION}:{namespace}",
            ttl,
        )
        self.paper_cache = paper_cache

    async def get(self, key: str) -> list[Paper] | None:
        value = await self.redis.get(f"scholar:search:{self.namespace}:{key}")
        return None if value is None else _PAPERS.validate_json(value)

    async def put(self, key: str, papers: list[Paper]) -> None:
        await self.redis.set(
            f"scholar:search:{self.namespace}:{key}", orjson.dumps(papers), ex=self.ttl
        )
        if self.paper_cache is not None:
            await self.paper_cache.index_titles([
                (paper.title, paper_identifiers(paper)) for paper in papers if paper.title
            ])
