"""Redis 共享论文富化缓存；公共来源按身份共享，BYOK 来源按凭证指纹隔离。"""

from __future__ import annotations

from pydantic import TypeAdapter
from redis.asyncio import Redis

from scholar.domain.models.identifiers import PaperIdentifier
from scholar.domain.repositories.paper_enrichment_cache import CachedPaperEnrichment

_ENRICHMENT = TypeAdapter(CachedPaperEnrichment)


class RedisPaperEnrichmentCache:
    def __init__(self, redis: Redis, namespace: str):
        self.redis, self.namespace = redis, namespace

    async def get(
        self, identifiers: list[PaperIdentifier], *, scope: str | None = None
    ) -> CachedPaperEnrichment | None:
        keys = [self._key(identifier, scope) for identifier in identifiers]
        values = await self.redis.mget(keys)
        cached = next((value for value in values if value is not None), None)
        return None if cached is None else _ENRICHMENT.validate_json(cached)

    async def put(
        self,
        identifiers: list[PaperIdentifier],
        value: CachedPaperEnrichment,
        *,
        ttl_seconds: int,
        scope: str | None = None,
    ) -> None:
        payload = _ENRICHMENT.dump_json(value)
        # 统一事务写入身份别名，避免部分更新把两个不同论文身份接在一起。
        async with self.redis.pipeline(transaction=True) as pipeline:
            for identifier in dict.fromkeys(identifiers):
                await pipeline.set(self._key(identifier, scope), payload, ex=ttl_seconds)
            await pipeline.execute()

    async def invalidate(self, identifiers: list[PaperIdentifier]) -> None:
        """删除请求身份及其缓存记录声明的全部别名。"""
        if not identifiers:
            return
        keys = [self._key(identifier) for identifier in dict.fromkeys(identifiers)]
        values = await self.redis.mget(keys)
        aliases = list(dict.fromkeys(identifiers))
        for value in values:
            if value is not None:
                record = _ENRICHMENT.validate_json(value)
                aliases.extend(record.cache_aliases)
        await self.redis.delete(
            *(self._key(identifier) for identifier in dict.fromkeys(aliases))
        )

    def _key(self, identifier: PaperIdentifier, scope: str | None = None) -> str:
        return (
            f"scholar:enrichment:{self.namespace}{':' + scope if scope else ''}:"
            f"{identifier.kind.value}:{identifier.value}"
        )
