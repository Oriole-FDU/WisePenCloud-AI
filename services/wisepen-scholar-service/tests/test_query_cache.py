from datetime import date

import orjson
import pytest
from scholar.core.storage.redis.query_cache_repository import RedisQueryCacheRepository
from scholar.domain.models.paper import Paper, PaperSource


class RedisSpy:
    def __init__(self):
        self.values = {}
        self.set_calls = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, ex):
        self.values[key] = value
        self.set_calls.append((key, value, ex))


@pytest.mark.asyncio
async def test_query_cache_versions_index_namespace_and_roundtrips_papers():
    redis = RedisSpy()
    paper = Paper(
        arxiv_id="2401.00001",
        doi=None,
        title="A paper",
        abstract="Abstract",
        authors=["Author"],
        publication_date=date(2024, 1, 2),
        year=2024,
        url="https://arxiv.org/abs/2401.00001",
        pdf_url=None,
        source=PaperSource.ARXIV,
    )
    redis.values["scholar:search:scholar-index:query-key"] = orjson.dumps([paper])
    cache = RedisQueryCacheRepository(redis, "scholar-index", ttl=600)

    assert await cache.get("query-key") is None

    await cache.put("query-key", [paper])
    other_index = RedisQueryCacheRepository(redis, "other-index", ttl=600)

    assert await cache.get("query-key") == [paper]
    assert await other_index.get("query-key") is None
    assert redis.set_calls[0][0] == (
        "scholar:search:compact-v1:scholar-index:query-key"
    )
    assert redis.set_calls[0][2] == 600
