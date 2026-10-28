"""完整论文查询缓存；来源命名空间内的身份别名共享同一份载荷。"""

from __future__ import annotations

from pydantic import TypeAdapter
from redis.asyncio import Redis

from scholar.core.storage.redis import LOOKUP_CACHE_REVISION
from scholar.domain.models.identifiers import PaperIdentifier
from scholar.domain.repositories.paper_lookup_cache import CachedPaperLookup

_LOOKUP = TypeAdapter(CachedPaperLookup)


class RedisPaperLookupCache:
    def __init__(self, redis: Redis, namespace: str):
        self.redis, self.namespace = redis, f"{LOOKUP_CACHE_REVISION}:{namespace}"

    async def get_many(
            self, ids: list[PaperIdentifier]
    ) -> list[CachedPaperLookup | None]:
        if not ids:
            return []
        values = await self.redis.mget([self._key(identifier) for identifier in ids])
        return [
            None if value is None else _LOOKUP.validate_json(value) for value in values
        ]

    async def put_many(self, entries: list[tuple[CachedPaperLookup, int]]) -> None:
        if not entries:
            return
        # 一个批次的完整记录和全部别名一起写入，避免同篇论文的别名只发布一部分。
        async with self.redis.pipeline(transaction=True) as pipeline:
            for record, ttl_seconds in entries:
                payload = _LOOKUP.dump_json(record)
                for identifier in record.cache_aliases:
                    await pipeline.set(self._key(identifier), payload, ex=ttl_seconds)
            await pipeline.execute()

    async def invalidate(self, identifiers: list[PaperIdentifier]) -> None:
        """同时删除已知身份和缓存中声明的全部别名。"""
        if not identifiers:
            return
        aliases = list(dict.fromkeys(identifiers))
        records = await self.get_many(aliases)
        for record in records:
            if record is not None:
                aliases.extend(record.cache_aliases)
        await self.redis.delete(
            *(self._key(identifier) for identifier in dict.fromkeys(aliases))
        )

    def _key(self, identifier: PaperIdentifier) -> str:
        return f"scholar:lookup:{self.namespace}:{identifier.kind.value}:{identifier.value}"
