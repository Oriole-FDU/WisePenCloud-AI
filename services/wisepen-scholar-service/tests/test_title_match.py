"""从批量 match 入口验证身份解析、外部失败隔离和既有 lookup 复用。"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from policy_fakes import make_policy_store
from pdf_fakes import make_pdf_service
from title_fakes import MemoryRedis, MemoryTitleCache
from scholar.application.lookup import PaperLookupService
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.match import MatchStatus, PaperMatch, PaperMatchService
from scholar.core.providers import PROVIDER_POLICIES
from scholar.core.providers.crossref import CrossrefTitleResolver
from scholar.core.storage.redis.paper_cache import RedisPaperLookupCache
from scholar.core.storage.redis.query_cache_repository import RedisQueryCacheRepository
from scholar.core.storage.redis.title_cache import RedisTitleCache
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.identifiers import parse_identifier
from scholar.domain.models.paper import Paper
from scholar.domain.models.titles import normalize_title
from scholar.domain.repositories.paper_lookup_cache import CachedPaperLookup


def paper(title="Attention Is All You Need", arxiv="1706.03762", doi=None):
    return Paper(arxiv, doi, title, "abstract", ["Author"], None, 2017,
                 "https://example.test/paper", "https://example.test/paper.pdf", "arxiv")


class Provider:
    def __init__(self, papers):
        self.papers, self.calls = papers, []

    async def lookup(self, identities, *, api_key=None):
        self.calls.append((identities, api_key))
        return [PaperLookupMatch([self.papers[identity]]) if identity in self.papers else PaperLookupMatch([]) for identity in identities]


def lookup(papers):
    provider = Provider(papers)
    service = PaperLookupService(
        LookupRunner({"arxiv": LookupBinding(provider, None, replace(PROVIDER_POLICIES["arxiv"], identifier_kinds=list({i.kind for i in papers})))}),
        pdf=make_pdf_service(), policies=make_policy_store(),
    )
    return service, provider


def test_title_normalization_preserves_words_subtitles_and_math():
    assert normalize_title('  ＢＥＲＴ: “Pre-training”\n— Language  ') == normalize_title('bert: "pre-training" - language')
    assert normalize_title("BERT: Pre-training") != normalize_title("BERT")
    assert normalize_title("Attention Is All You Need") != normalize_title("Attention when you need")
    assert normalize_title("A + B") != normalize_title("A - B")
    assert normalize_title("A − B") != normalize_title("A - B")


@pytest.mark.asyncio
async def test_title_index_keeps_collisions_and_merges_identity_aliases():
    redis = MemoryRedis()
    cache = RedisTitleCache(redis, ttl=86400)
    first, second = map(parse_identifier, ["1706.03762", "1810.04805"])
    doi = parse_identifier("10.1000/attention")
    await cache.put_many([("Same title", [first, doi]), ("SAME TITLE", [doi]), ("same title", [first])])
    matched = (await cache.get_many([" same   title "]))[0]
    assert len(matched.candidates) == 1
    assert set(matched.candidates[0]) == {first, doi}
    await cache.put_many([("same title", [second])])
    assert len((await cache.get_many(["Same title"]))[0].candidates) == 2
    assert set(redis.ttls.values()) == {86400}


@pytest.mark.asyncio
async def test_source_and_search_cache_writes_build_shared_title_index():
    redis = MemoryRedis()
    index = RedisTitleCache(redis, ttl=86400)
    query = RedisQueryCacheRepository(redis, "test", 600, title_cache=index)
    full = RedisPaperLookupCache(redis, "test:arxiv", title_cache=index)
    p = paper()
    identity = parse_identifier(p.arxiv_id)
    await query.put("search", [p])
    assert (await index.get_many([p.title]))[0].candidates == [[identity]]
    await full.put_many([(CachedPaperLookup(p, (identity,)), 86400)])
    assert (await full.get_many([identity]))[0].paper == p
    assert len((await index.get_many([p.title]))[0].candidates) == 1


@pytest.mark.asyncio
async def test_batch_match_uses_cache_then_qdrant_then_crossref_and_preserves_positions():
    cached, local, external = paper("Cached"), paper("Local", "1810.04805"), paper("External", None, "10.1000/external")
    identities = [parse_identifier(p.arxiv_id or p.doi) for p in (cached, local, external)]
    cache = MemoryTitleCache()
    await cache.put_many([("Cached", [identities[0]])])
    repository = SimpleNamespace(match_titles=AsyncMock(return_value=[PaperLookupMatch([local]), PaperLookupMatch([])]))
    calls = []

    async def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"message": {"items": [{"DOI": external.doi, "title": ["External"]}]}})

    fetch, provider = lookup(dict(zip(identities, [cached, local, external])))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        service = PaperMatchService(cache, repository, CrossrefTitleResolver(http, "jzsun@example.test"), fetch)
        result = await service.match(PaperMatch(["Cached", "Local", "External", " CACHED "]))
    assert [item.status for item in result.items] == [MatchStatus.FOUND] * 4
    assert [item.paper.title for item in result.items] == ["Cached", "Local", "External", "Cached"]
    repository.match_titles.assert_awaited_once_with(["Local", "External"])
    assert [request.url.params["query.bibliographic"] for request in calls] == ["External"]
    assert sum(len(ids) for ids, _ in provider.calls) == 3
    assert len((await cache.get_many(["External"]))[0].candidates) == 1


@pytest.mark.asyncio
async def test_ambiguous_cache_does_not_choose_a_paper_or_call_later_layers():
    cache = MemoryTitleCache()
    await cache.put_many([("Same", [parse_identifier("1706.03762")]), ("Same", [parse_identifier("1810.04805")])])
    local = SimpleNamespace(match_titles=AsyncMock())
    crossref = SimpleNamespace(match=AsyncMock())
    fetch = SimpleNamespace(lookup=AsyncMock())
    result = await PaperMatchService(cache, local, crossref, fetch).match(PaperMatch(["Same"]))
    assert result.items[0].status == MatchStatus.AMBIGUOUS
    assert result.items[0].paper is None
    local.match_titles.assert_not_awaited()
    crossref.match.assert_not_awaited()
    fetch.lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_crossref_filters_non_equal_titles_and_keeps_item_rate_limits():
    async def handle(request):
        title = request.url.params["query.bibliographic"]
        if title == "Limited":
            return httpx.Response(429)
        return httpx.Response(200, json={"message": {"items": [
            {"DOI": "10.1000/near", "title": ["Another title"]},
            {"DOI": "10.1000/exact", "title": ["Exact"]},
        ]}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        matches = await CrossrefTitleResolver(http, "contact@example.test").match(["Exact", "Missing", "Limited"])
    assert matches[0].candidates == [[parse_identifier("10.1000/exact")]]
    assert matches[1].candidates == [] and matches[1].error is None
    assert "429" in matches[2].error


@pytest.mark.asyncio
async def test_crossref_semaphore_is_shared_between_batches():
    active, peak = 0, 0
    two_entered, release = asyncio.Event(), asyncio.Event()

    async def handle(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            two_entered.set()
        await release.wait()
        active -= 1
        return httpx.Response(200, json={"message": {"items": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        resolver = CrossrefTitleResolver(http, "contact@example.test")
        first = asyncio.create_task(resolver.match(["A", "B", "C"]))
        second = asyncio.create_task(resolver.match(["D", "E", "F"]))
        await asyncio.wait_for(two_entered.wait(), 1)
        release.set()
        await asyncio.gather(first, second)
    assert peak == 2


@pytest.mark.asyncio
async def test_crossref_timeout_preserves_cached_paper_and_all_failures_remain_items():
    cached = paper("Cached")
    identity = parse_identifier(cached.arxiv_id)
    cache = MemoryTitleCache()
    await cache.put_many([("Cached", [identity])])
    local = SimpleNamespace(match_titles=AsyncMock(return_value=[PaperLookupMatch([])]))
    fetch, _ = lookup({identity: cached})

    async def handle(request):
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        service = PaperMatchService(cache, local, CrossrefTitleResolver(http, "contact@example.test"), fetch)
        result = await service.match(PaperMatch(["Cached", "Waiting"]), timeout=0.05)
        failed = await service.match(PaperMatch(["Waiting"]), timeout=0.01)
    assert result.items[0].status == MatchStatus.FOUND
    assert result.items[1].status == MatchStatus.FAILED
    assert failed.items[0].status == MatchStatus.FAILED


@pytest.mark.asyncio
async def test_programming_errors_are_not_hidden_as_missing_titles():
    local = SimpleNamespace(match_titles=AsyncMock(side_effect=TypeError("bug")))
    service = PaperMatchService(MemoryTitleCache(), local, SimpleNamespace(), SimpleNamespace())
    with pytest.raises(TypeError, match="bug"):
        await service.match(PaperMatch(["Title"]))
