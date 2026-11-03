"""完整标题召回必须独立于普通候选，并在融合后、最终数量裁剪前优先返回。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from qdrant_client import AsyncQdrantClient, models

from pdf_fakes import make_pdf_service
from policy_fakes import make_policy_store
from title_fakes import MemoryRedis
from scholar.application.lookup import PaperLookup, PaperLookupService
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.search import PaperSearch, PaperSearchService, SearchBinding, SourceScope
from scholar.core.providers import PROVIDER_POLICIES, ArxivProvider
from scholar.core.storage.qdrant.paper_repository import QdrantPaperRepository
from scholar.core.storage.redis.paper_cache import RedisPaperCache
from scholar.core.storage.redis.query_cache import RedisQueryCacheRepository
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.models.fields import AcademicField, FieldPolicy
from scholar.domain.models.identifiers import parse_identifier
from scholar.domain.models.paper import Paper
from offline.publish import point_payload


def paper(title, identifier="1706.03762", source="arxiv"):
    return Paper(identifier, None, title, "abstract", ["Author"], None, 2017,
                 "https://example.test/paper", "https://example.test/paper.pdf", source)


def service(searchers):
    policies = make_policy_store({AcademicField.GENERAL: FieldPolicy(list(searchers))})
    return PaperSearchService(searchers, policies, RedisQueryCacheRepository(MemoryRedis(), "test", 600),
                              LookupRunner({}), make_pdf_service())


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["Attention Is All You Need", "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"])
@pytest.mark.parametrize("scope", [SourceScope.DEFAULT, SourceScope.MULTI_SOURCE])
async def test_exact_title_is_recalled_when_missing_from_ordinary_candidates(title, scope):
    ordinary = SimpleNamespace(search=AsyncMock(return_value=PaperSearchBatch([
        paper("Attention when you need", "2501.00001", "openalex"),
        paper("A related survey", "2501.00002", "openalex"),
    ])))
    arxiv = SimpleNamespace(search=AsyncMock(return_value=PaperSearchBatch([])))
    title_match = AsyncMock(return_value=PaperSearchBatch([paper(title)]))
    search = service({"openalex": SearchBinding(ordinary), "arxiv": SearchBinding(arxiv, title_matcher=title_match)})
    result = await search.search(PaperSearch(title, limit=1, source_scope=scope))
    assert [p.title for p in result.papers] == [title]
    if scope == SourceScope.DEFAULT:
        arxiv.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_exact_identity_priority_survives_a_different_display_title_after_fusion():
    title = "Attention Is All You Need"
    related = paper("Related", "2501.00001")
    published = paper("A publisher title variant", source="openalex")
    ordinary = SimpleNamespace(search=AsyncMock(return_value=PaperSearchBatch([related, published])))
    arxiv = SimpleNamespace(search=AsyncMock(return_value=PaperSearchBatch([related])))
    exact = AsyncMock(return_value=PaperSearchBatch([paper(title)]))
    result = await service({"openalex": SearchBinding(ordinary), "arxiv": SearchBinding(arxiv, title_matcher=exact)}).search(
        PaperSearch(title, limit=1, source_scope=SourceScope.MULTI_SOURCE)
    )
    assert result.papers[0].arxiv_id == "1706.03762"
    assert result.papers[0].title == published.title


@pytest.mark.asyncio
async def test_exact_title_priority_applies_to_external_candidates_before_limit():
    title = "Attention Is All You Need"
    ordinary = SimpleNamespace(search=AsyncMock(return_value=PaperSearchBatch([
        paper("Related", "2501.00001", "openalex"), paper(title, source="openalex"),
    ])))
    result = await service({"openalex": SearchBinding(ordinary)}).search(PaperSearch(title, limit=1))
    assert result.papers[0].title == title


@pytest.mark.asyncio
@pytest.mark.parametrize("has_exact", [True, False])
async def test_exact_paper_survives_ordinary_failure_but_empty_exact_channel_does_not_mask_it(has_exact):
    ordinary = SimpleNamespace(search=AsyncMock(side_effect=ProviderError("ordinary offline")))
    exact = AsyncMock(return_value=PaperSearchBatch([paper("Attention Is All You Need")] if has_exact else []))
    search = service({"arxiv": SearchBinding(ordinary, title_matcher=exact)})
    if not has_exact:
        with pytest.raises(ProviderError, match="ordinary offline"):
            await search.search(PaperSearch("Attention Is All You Need"))
    else:
        result = await search.search(PaperSearch("Attention Is All You Need"))
        assert result.papers[0].arxiv_id == "1706.03762"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["arxiv", "huggingface"])
async def test_search_warms_complete_source_cache_but_not_hf_overview(source):
    redis = MemoryRedis()
    full = RedisPaperCache(redis, "test")
    p = paper("Attention Is All You Need", source=source)
    provider = SimpleNamespace(search=AsyncMock(return_value=PaperSearchBatch([p])), lookup=AsyncMock())
    policies = make_policy_store({AcademicField.GENERAL: FieldPolicy([source])})
    runner = LookupRunner({source: LookupBinding(provider, full, PROVIDER_POLICIES[source])})
    search = PaperSearchService({source: SearchBinding(provider)}, policies,
                               RedisQueryCacheRepository(redis, "test", 600, paper_cache=full), runner, make_pdf_service())
    await search.search(PaperSearch(p.title))
    identity = parse_identifier(p.arxiv_id)
    if source == "arxiv":
        lookup = PaperLookupService(runner, pdf=make_pdf_service(), policies=policies)
        result = await lookup.lookup(PaperLookup([p.arxiv_id]))
        assert result.items[0].paper.title == p.title
        provider.lookup.assert_not_awaited()
    else:
        assert await full.get_many(source, [identity]) == [None]
    assert len((await full.match_titles([p.title], [source]))[0].papers) == (1 if source == "arxiv" else 0)


@pytest.mark.asyncio
async def test_qdrant_full_title_matching_and_search_filters_use_same_payload_contract():
    client = AsyncQdrantClient(":memory:")
    try:
        await client.create_collection("titles", vectors_config={})
        raw = {
            "arxiv_id": "1706.03762", "doi": None, "raw_id": "1706.03762", "title": "Attention Is All You Need",
            "abstract": "abstract", "authors": ["Author"], "submitted_date": "2017-06-12",
            "updated_date": "2023-01-01", "versions": [], "journal_ref": None, "comments": "Author notes",
            "license": None, "url": "https://arxiv.org/abs/1706.03762", "pdf_url": "https://arxiv.org/pdf/1706.03762",
            "categories": ["cs.CL"],
        }
        await client.upsert("titles", points=[models.PointStruct(id=1, vector={}, payload=point_payload(raw))])
        repository = QdrantPaperRepository(client, "titles")
        exact = await repository.match_titles([" attention is ALL you need "])
        assert exact[0].papers[0].arxiv_id == raw["arxiv_id"]
        provider = ArxivProvider(repository, None)
        from datetime import date
        included = await provider.match_titles(query=raw["title"], field=AcademicField.COMPUTER_SCIENCE,
                                              published_after=date(2017, 6, 12), published_before=date(2017, 6, 12), limit=20)
        wrong_field = await provider.match_titles(query=raw["title"], field=AcademicField.PHYSICS,
                                                 published_after=None, published_before=None, limit=20)
        later = await provider.match_titles(query=raw["title"], field=AcademicField.COMPUTER_SCIENCE,
                                           published_after=date(2020, 1, 1), published_before=None, limit=20)
        assert len(included.papers) == 1
        assert wrong_field.papers == later.papers == []
    finally:
        await client.close()
