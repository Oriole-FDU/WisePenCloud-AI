"""从批量 match 入口验证身份解析、外部失败隔离和既有 lookup 复用。"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from policy_fakes import make_policy_store
from pdf_fakes import make_pdf_service
from title_fakes import MemoryRedis
from scholar.application.lookup import PaperLookupService
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.match import MatchStatus, PaperMatch, PaperMatchService
from scholar.core.providers import PROVIDER_POLICIES
from scholar.core.providers.crossref import CrossrefTitleResolver
from scholar.core.storage.redis.paper_cache import RedisPaperCache
from scholar.core.storage.redis.query_cache import RedisQueryCacheRepository
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.identifiers import IdentifierKind, parse_identifier
from scholar.domain.models.paper import Paper
from scholar.domain.interfaces.title_resolver import TitleMatch
from scholar.utils import normalize_title
from scholar.domain.repositories.paper_cache import CachedPaperLookup


def paper(title="Attention Is All You Need", arxiv="1706.03762", doi=None):
    return Paper(arxiv, doi, title, "abstract", ["Author"], None, 2017,
                 "https://example.test/paper", "https://example.test/paper.pdf", "arxiv")


class Provider:
    def __init__(self, papers):
        self.papers, self.calls = papers, []

    async def lookup(self, identities, *, api_key=None):
        self.calls.append((identities, api_key))
        return [PaperLookupMatch([self.papers[identity]]) if identity in self.papers else PaperLookupMatch([]) for identity in identities]


def lookup(papers, cache=None):
    provider = Provider(papers)
    service = PaperLookupService(
        LookupRunner({"arxiv": LookupBinding(provider, cache, replace(PROVIDER_POLICIES["arxiv"], identifier_kinds=IdentifierKind.members()))}),
        pdf=make_pdf_service(), policies=make_policy_store(),
    )
    return service, provider


def test_title_normalization_preserves_words_subtitles_and_math():
    assert normalize_title('  ＢＥＲＴ: “Pre-training”\n— Language  ') == normalize_title('bert: "pre-training" - language')
    assert normalize_title("BERT: Pre-training") != normalize_title("BERT")
    assert normalize_title("Attention Is All You Need") != normalize_title("Attention when you need")
    assert normalize_title("A + B") != normalize_title("A - B")
    assert normalize_title("A − B") != normalize_title("A - B")


@pytest.fixture
def cache():
    return RedisPaperCache(MemoryRedis(), "test")


@pytest.mark.asyncio
async def test_paper_cache_keeps_title_collisions_and_identity_aliases(cache):
    first = paper("Same title", doi="10.1000/attention")
    second = paper("Same title", "1810.04805")
    aliases = (parse_identifier(first.arxiv_id), parse_identifier(first.doi))
    await cache.put_many("arxiv", [(CachedPaperLookup(first, aliases), 86400)])
    await cache.index_titles([("SAME TITLE", [aliases[1]])])
    matched = (await cache.match_titles([" same   title "], ["arxiv"]))[0]
    assert matched.papers == [first]
    await cache.put_many("arxiv", [(CachedPaperLookup(second, (parse_identifier(second.arxiv_id),)), 86400)])
    assert len((await cache.match_titles(["Same title"], ["arxiv"]))[0].papers) == 2


@pytest.mark.asyncio
async def test_search_overview_indexes_title_without_becoming_a_full_paper_hit(cache):
    query = RedisQueryCacheRepository(cache.redis, "test", 600, paper_cache=cache)
    p = paper()
    await query.put("search", [p])
    assert (await cache.match_titles([p.title], ["arxiv"]))[0].papers == []
    await cache.put_many("arxiv", [(CachedPaperLookup(p, (parse_identifier(p.arxiv_id),)), 86400)])
    assert (await cache.match_titles([p.title], ["arxiv"]))[0].papers == [p]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["put_many", "index_titles"])
async def test_cache_write_failure_preserves_local_and_crossref_results(cache, operation, failed_cache_write):
    local = paper("Local")
    external = paper("External", None, "10.1000/external")
    repository = SimpleNamespace(match_titles=AsyncMock(return_value=[PaperLookupMatch([local]), PaperLookupMatch([])]))
    crossref = SimpleNamespace(match=AsyncMock(return_value=[TitleMatch([[parse_identifier(external.doi)]])]))
    fetch, _ = lookup({parse_identifier(external.doi): external})
    setattr(cache, operation, failed_cache_write)
    service = PaperMatchService(cache, repository, crossref, fetch)

    result = await service.match(PaperMatch(["Local", "External"]), timeout=0.05)

    assert [item.status for item in result.items] == [MatchStatus.FOUND, MatchStatus.FOUND]
    assert [item.paper.title for item in result.items] == ["Local", "External"]


@pytest.mark.asyncio
async def test_batch_match_uses_full_cache_then_local_then_crossref_and_preserves_positions(cache):
    cached, local, external = paper("Cached"), paper("Local", "1810.04805"), paper("External", None, "10.1000/external")
    identity = parse_identifier(cached.arxiv_id)
    await cache.put_many("arxiv", [(CachedPaperLookup(cached, (identity,)), 86400)])
    repository = SimpleNamespace(match_titles=AsyncMock(return_value=[PaperLookupMatch([local]), PaperLookupMatch([])]))
    calls = []

    async def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"message": {"items": [{"DOI": external.doi, "title": ["External"]}]}})

    fetch, provider = lookup({parse_identifier(external.doi): external}, cache)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        service = PaperMatchService(cache, repository, CrossrefTitleResolver(http, "jzsun@example.test"), fetch)
        result = await service.match(PaperMatch(["Cached", "Local", "External", " CACHED "]))
        repeated = await service.match(PaperMatch(["Local", "External"]))
    assert [item.status for item in result.items] == [MatchStatus.FOUND] * 4
    assert [item.paper.title for item in result.items] == ["Cached", "Local", "External", "Cached"]
    assert result.items[1].paper.doi == "10.48550/arxiv.1810.04805"
    assert [item.status for item in repeated.items] == [MatchStatus.FOUND] * 2
    repository.match_titles.assert_awaited_once_with(["Local", "External"])
    assert [request.url.params["query.bibliographic"] for request in calls] == ["External"]
    assert provider.calls == [([parse_identifier(external.doi)], None)]


@pytest.mark.asyncio
async def test_ambiguous_cache_does_not_choose_a_paper_or_call_later_layers(cache):
    for identity in ("1706.03762", "1810.04805"):
        await cache.put_many("arxiv", [(CachedPaperLookup(paper("Same", identity), (parse_identifier(identity),)), 86400)])
    local = SimpleNamespace(match_titles=AsyncMock())
    crossref = SimpleNamespace(match=AsyncMock())
    fetch, provider = lookup({})
    result = await PaperMatchService(cache, local, crossref, fetch).match(PaperMatch(["Same"]))
    assert result.items[0].status == MatchStatus.AMBIGUOUS
    assert result.items[0].paper is None
    local.match_titles.assert_not_awaited()
    crossref.match.assert_not_awaited()
    assert provider.calls == []


@pytest.mark.asyncio
async def test_stale_title_index_continues_through_local_and_crossref(cache):
    identity = parse_identifier("1706.03762")
    await cache.index_titles([("Stale", [identity])])
    local = SimpleNamespace(match_titles=AsyncMock(return_value=[PaperLookupMatch([])]))
    crossref = SimpleNamespace(match=AsyncMock(return_value=[TitleMatch()]))
    fetch, provider = lookup({})
    result = await PaperMatchService(cache, local, crossref, fetch).match(PaperMatch(["Stale"]))
    assert result.items[0].status == MatchStatus.NOT_FOUND
    local.match_titles.assert_awaited_once_with(["Stale"])
    crossref.match.assert_awaited_once()
    assert provider.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [True, False])
async def test_title_cache_failure_is_audited_and_later_resolution_has_no_limitations(cache, monkeypatch, found):
    from redis.exceptions import RedisError

    audited = []
    monkeypatch.setattr("scholar.application.match.warning", lambda event, **fields: audited.append(fields))
    failure = RedisError("private cache diagnostic")
    monkeypatch.setattr(cache, "match_titles", AsyncMock(side_effect=failure))
    local = SimpleNamespace(match_titles=AsyncMock(return_value=[PaperLookupMatch([paper("Known")] if found else [])]))
    crossref = SimpleNamespace(match=AsyncMock(return_value=[TitleMatch()]))
    fetch, _ = lookup({})
    result = await PaperMatchService(cache, local, crossref, fetch).match(PaperMatch(["Known"]))
    assert result.items[0].status == (MatchStatus.FOUND if found else MatchStatus.NOT_FOUND)
    assert result.items[0].limitations == []
    assert any(fields.get("exc") is failure for fields in audited)


@pytest.mark.asyncio
async def test_full_cached_paper_assembles_domain_and_pdf_without_external_calls(cache):
    base = paper("Complete", None, "10.1000/complete")
    base.pdf_url = None
    base.authors = ["First", "Second"]
    identity = parse_identifier(base.doi)
    await cache.put_many("arxiv", [(CachedPaperLookup(base, (identity,)), 86400)])
    enriched = replace(base, source="s2", metadata={"s2": {"citation_count": 0}})
    await cache.put_many("s2", [(CachedPaperLookup(enriched, (identity,)), 86400)])
    await cache.put_pdf_many([(base.doi, "https://example.test/recovered.pdf", 86400)])
    fetch, provider = lookup({})
    result = await PaperMatchService(cache, SimpleNamespace(), SimpleNamespace(), fetch).match(PaperMatch([base.title]))
    assert result.items[0].paper.pdf_url == "https://example.test/recovered.pdf"
    assert result.items[0].paper.metadata["s2"]["citation_count"] == 0
    assert provider.calls == []
    raw = (await cache.get_many("arxiv", [identity]))[0].paper
    assert raw.authors == ["First", "Second"] and raw.pdf_url is None


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
    assert isinstance(matches[2].error, httpx.HTTPStatusError)
    assert matches[2].error.response.status_code == 429


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
async def test_crossref_timeout_preserves_cached_paper_and_all_failures_remain_items(cache):
    cached = paper("Cached")
    identity = parse_identifier(cached.arxiv_id)
    await cache.put_many("arxiv", [(CachedPaperLookup(cached, (identity,)), 86400)])
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
async def test_programming_errors_are_not_hidden_as_missing_titles(cache):
    local = SimpleNamespace(match_titles=AsyncMock(side_effect=TypeError("bug")))
    fetch, _ = lookup({})
    service = PaperMatchService(cache, local, SimpleNamespace(), fetch)
    with pytest.raises(TypeError, match="bug"):
        await service.match(PaperMatch(["Title"]))


@pytest.mark.asyncio
async def test_mixed_batch_preserves_cache_local_and_lookup_successes_with_external_failures(cache):
    cached, local = paper("Cached"), paper("Local", "1810.04805")
    remote = paper("Remote", None, "10.5555/remote")
    await cache.put_many("arxiv", [(CachedPaperLookup(cached, (parse_identifier(cached.arxiv_id),)), 86400)])
    repository = SimpleNamespace(match_titles=AsyncMock(return_value=[
        PaperLookupMatch([local]), PaperLookupMatch([]), PaperLookupMatch([]), PaperLookupMatch([]), PaperLookupMatch([]),
    ]))
    crossref = SimpleNamespace(match=AsyncMock(return_value=[
        TitleMatch([[parse_identifier(remote.doi)]]),
        TitleMatch(error=httpx.HTTPStatusError("quota diagnostic", request=httpx.Request("GET", "https://crossref.test"), response=httpx.Response(429))), TitleMatch(),
        TitleMatch([[parse_identifier("10.5555/first")], [parse_identifier("10.5555/second")]]),
    ]))
    fetch, provider = lookup({parse_identifier(remote.doi): remote}, cache)
    result = await PaperMatchService(cache, repository, crossref, fetch).match(
        PaperMatch(["Cached", "Local", "Remote", "Limited", "Missing", "Same", " CACHED "])
    )
    assert [item.status for item in result.items] == [
        MatchStatus.FOUND, MatchStatus.FOUND, MatchStatus.FOUND, MatchStatus.FAILED,
        MatchStatus.NOT_FOUND, MatchStatus.AMBIGUOUS, MatchStatus.FOUND,
    ]
    assert [(item.source, item.reason) for item in result.items[3].limitations] == [("crossref", "rate_limited")]
    assert provider.calls == [([parse_identifier(remote.doi)], None)]


@pytest.mark.asyncio
async def test_crossref_batch_error_keeps_already_found_papers(cache):
    from scholar.domain.errors import ProviderError
    cached = paper("Cached")
    await cache.put_many("arxiv", [(CachedPaperLookup(cached, (parse_identifier(cached.arxiv_id),)), 86400)])
    repository = SimpleNamespace(match_titles=AsyncMock(return_value=[PaperLookupMatch([])]))
    crossref = SimpleNamespace(match=AsyncMock(side_effect=ProviderError("crossref unavailable")))
    fetch, _ = lookup({})
    result = await PaperMatchService(cache, repository, crossref, fetch).match(PaperMatch(["Cached", "Missing"]))
    assert [item.status for item in result.items] == [MatchStatus.FOUND, MatchStatus.FAILED]
