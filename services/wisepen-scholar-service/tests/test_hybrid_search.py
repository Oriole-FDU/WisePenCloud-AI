"""本地融合、有限先验和编码降级的公共来源/用例合同。"""

from dataclasses import replace
from datetime import date

import httpx
import pytest
from openai import APIConnectionError
from scholar.application.field_policies import FieldPolicy
from scholar.application.lookup_runner import LookupRunner
from scholar.application.services.search import (
    PaperSearch,
    PaperSearchService,
    SearchBinding,
)
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.paper import Paper
from scholar.domain.models.source_execution import SourceExecutionStatus
from scholar.providers.arxiv import ArxivProvider


def paper(identifier, metrics=None):
    return Paper(
        identifier,
        None,
        identifier,
        "Abstract",
        [],
        date(2025, 1, 1),
        2025,
        "https://arxiv.org",
        None,
        "arxiv",
        {"s2": metrics or {}},
    )


class Repository:
    def __init__(self, lexical, semantic):
        self.lexical, self.semantic = lexical, semantic

    async def search(self, **kwargs):
        return self.lexical

    async def search_vector(self, **kwargs):
        return self.semantic


class Encoder:
    error = None

    async def embed(self, *, texts):
        if self.error is not None:
            raise self.error
        return [[1.0] * 768]


class Cache:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def put(self, key, papers):
        self.values[key] = papers


async def search(provider):
    return await provider.search(
        query="reasoning",
        field=AcademicField.COMPUTER_SCIENCE,
        published_after=None,
        published_before=None,
        limit=20,
    )


@pytest.mark.asyncio
async def test_rrf_combines_independent_ranks_deduplicates_and_keeps_stable_ties():
    a, b, c = [paper(f"2501.{i:05d}") for i in range(1, 4)]
    result = await search(ArxivProvider(Repository([a, b, c], [b, a]), Encoder()))
    assert [p.arxiv_id for p in result.papers] == [a.arxiv_id, b.arxiv_id, c.arxiv_id]
    assert result.partial_error is None


@pytest.mark.asyncio
async def test_citation_bonus_preserves_zero_missing_and_cannot_overturn_large_rank_gap():
    plain = paper("2501.00001")
    important = paper(
        "2501.00002", {"citation_count": 100, "influential_citation_count": 10}
    )
    result = await search(ArxivProvider(Repository([plain], [important]), Encoder()))
    assert [paper.arxiv_id for paper in result.papers] == [
        important.arxiv_id,
        plain.arxiv_id,
    ]
    zero = replace(
        important,
        metadata={"s2": {"citation_count": 0, "influential_citation_count": 0}},
    )
    zero_result = await search(ArxivProvider(Repository([plain], [zero]), Encoder()))
    assert [paper.arxiv_id for paper in zero_result.papers] == [
        plain.arxiv_id,
        zero.arxiv_id,
    ]
    crowd = [paper(f"2502.{i:05d}") for i in range(1, 20)]
    huge = replace(
        important,
        metadata={"s2": {"citation_count": 10**9, "influential_citation_count": 10**9}},
    )
    result = await search(
        ArxivProvider(Repository([plain, *crowd, huge], []), Encoder())
    )
    assert result.papers[0].arxiv_id == plain.arxiv_id


@pytest.mark.asyncio
async def test_encoder_failure_is_visible_not_cached_and_recovery_restores_hybrid():
    encoder = Encoder()
    encoder.error = APIConnectionError(
        request=httpx.Request("POST", "http://encoder/v1/embeddings")
    )
    lexical, semantic = paper("2501.00001"), paper("2501.00002")
    provider = ArxivProvider(Repository([lexical], [semantic]), encoder)
    binding = SearchBinding(provider, cache_revision=provider.cache_revision)
    cache = Cache()
    service = PaperSearchService(
        {"arxiv": binding},
        {AcademicField.GENERAL: FieldPolicy(["arxiv"])},
        cache,
        LookupRunner({}),
    )
    query = PaperSearch("reasoning")
    degraded = await service.search(query)
    assert [paper.arxiv_id for paper in degraded.papers] == [lexical.arxiv_id]
    assert degraded.execution[0].status == SourceExecutionStatus.PARTIAL
    assert degraded.execution[0].error
    encoder.error = None
    recovered = await service.search(query)
    assert len(recovered.papers) == 2
    assert not recovered.execution[0].cache_hit
    assert (await service.search(query)).execution[0].cache_hit
    # 模拟平台调参后重新装配服务，旧排序缓存不能继续复用。
    adjusted = ArxivProvider(Repository([lexical], [semantic]), encoder, rrf_k=30)
    binding.searcher = adjusted
    binding.cache_revision = adjusted.cache_revision
    assert not (await service.search(query)).execution[0].cache_hit


@pytest.mark.asyncio
async def test_program_error_is_not_encoder_degradation():
    encoder = Encoder()
    encoder.error = TypeError("program bug")
    with pytest.raises(TypeError, match="program bug"):
        await search(ArxivProvider(Repository([], []), encoder))
