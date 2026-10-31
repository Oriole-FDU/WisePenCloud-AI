"""验证 lookup runner 的缓存、精确命中短路、错误和并发生命周期合同。"""
from policy_fakes import make_policy_store

from pdf_fakes import make_pdf_service

from dataclasses import replace
from scholar.core.providers import PROVIDER_POLICIES

import asyncio
from datetime import date

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.lookup import (
    LookupStatus,
    PaperLookup,
    PaperLookupService,
)
from scholar.core.storage.redis.paper_cache import RedisPaperLookupCache
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.identifiers import IdentifierKind, parse_identifier
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.models.fields import AcademicField, FieldPolicy
from scholar.domain.repositories.paper_lookup_cache import CachedPaperLookup
from scholar.core.providers.s2 import provider as s2
from scholar.core.providers.s2.provider import aliases as s2_aliases


class FakeRedisPipeline:
    """最小 Redis pipeline 替身，保留实际 JSON 序列化和 TTL 路径。"""

    def __init__(self, redis):
        self.redis = redis
        self.pending = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def set(self, key, value, *, ex):
        self.pending[key] = (value, ex)

    async def execute(self):
        for key, (value, ttl) in self.pending.items():
            self.redis.values[key] = value
            self.redis.ttls[key] = ttl


class FakeRedisBoundary:
    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.fail_delete = False

    async def mget(self, keys):
        return [self.values.get(key) for key in keys]

    async def delete(self, *keys):
        if self.fail_delete:
            raise RedisConnectionError("redis unavailable")
        for key in keys:
            self.values.pop(key, None)
            self.ttls.pop(key, None)

    def pipeline(self, *, transaction):
        return FakeRedisPipeline(self)


class FakeCache:
    """内存实现 lookup cache，允许注入 Redis 边界读取失败。"""

    def __init__(self):
        self.values = {}
        self.puts = []
        self.fail_get = False

    async def get_many(self, ids):
        if self.fail_get:
            raise RedisConnectionError("cache unavailable")
        return [self.values.get(identity) for identity in ids]

    async def put_many(self, entries):
        self.puts.extend(entries)
        for record, _ttl_seconds in entries:
            for identity in record.cache_aliases:
                self.values[identity] = record

    async def invalidate(self, identifiers):
        for identity in identifiers:
            self.values.pop(identity, None)


class FakeProvider:
    """完整 Paper provider，可控制批次失败与在途请求生命周期。"""

    def __init__(self, matches=None, *, error=None):
        self.matches = matches or {}
        self.error = error
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.block = False

    async def lookup(self, ids, *, api_key=None):
        ids = list(ids)
        self.calls.append((ids, api_key))
        self.entered.set()
        try:
            if self.block:
                await self.release.wait()
            if self.error is not None:
                raise self.error
            return [
                self.matches.get(identity, PaperLookupMatch([])) for identity in ids
            ]
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


def make_paper(
    arxiv_id="2401.00001",
    *,
    doi=None,
    source=PaperSource.S2,
    metadata=None,
    authors=None,
):
    return Paper(
        arxiv_id=arxiv_id,
        doi=doi,
        title="Offline title",
        abstract="Offline abstract",
        authors=["Offline author"] if authors is None else authors,
        publication_date=date(2024, 1, 2),
        year=2024,
        url="https://example.test/paper",
        pdf_url=None,
        source=source,
        metadata={} if metadata is None else metadata,
    )


def runner(provider, *, cache=None, extra_bindings=None):
    bindings = {
        PaperSource.S2: LookupBinding(provider, cache, replace(PROVIDER_POLICIES["s2"], identifier_kinds=IdentifierKind.members(), aliases=s2.aliases))
    }
    if extra_bindings:
        bindings.update(extra_bindings)
    return LookupRunner(bindings)


def service(provider, *, cache=None):
    return PaperLookupService(runner(provider, cache=cache), pdf=make_pdf_service(), policies=make_policy_store())


@pytest.mark.asyncio
async def test_redis_cache_roundtrips_full_paper_aliases_and_ttl_then_invalidates_all_aliases():
    redis = FakeRedisBoundary()
    cache = RedisPaperLookupCache(redis, "public-papers")
    arxiv = parse_identifier("2401.00001")
    doi = parse_identifier("10.5555/example")
    paper = make_paper(doi=doi.value, metadata={"s2": {"citation_count": 0}})
    record = CachedPaperLookup(paper, (arxiv, doi))

    await cache.put_many([(record, 7200)])

    assert await cache.get_many([doi, parse_identifier("2401.00002")]) == [
        record,
        None,
    ]
    assert set(redis.ttls.values()) == {7200}
    await cache.invalidate([doi])
    assert await cache.get_many([arxiv, doi]) == [None, None]
    assert redis.values == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("signals", [{}, {"citation_count": 0}])
async def test_negative_and_positive_lookup_results_use_distinct_ttls_and_cache_hits(signals):
    redis = FakeRedisBoundary()
    cache = RedisPaperLookupCache(redis, "ttl-contract")
    missing = parse_identifier("corpusid:404")
    found = parse_identifier("corpusid:123")
    paper = make_paper(
        metadata={
            "s2": {
                "paper_id": "a" * 40,
                "corpus_id": "123",
                **signals,
            }
        }
    )
    provider = FakeProvider({found: PaperLookupMatch([paper])})
    lookup = PaperLookupService(runner(provider, cache=cache), pdf=make_pdf_service(), policies=make_policy_store())

    absent = await lookup.lookup(PaperLookup(["corpusid:404"]))
    present = await lookup.lookup(PaperLookup(["corpusid:123"]))
    repeated = await lookup.lookup(PaperLookup(["corpusid:123"]))

    assert absent.items[0].status == LookupStatus.NOT_FOUND
    assert present.items[0].paper.title == repeated.items[0].paper.title
    assert provider.calls == [([missing], None), ([found], None)]
    assert set(redis.ttls.values()) == {3600, 86400}


@pytest.mark.asyncio
async def test_cache_read_failure_does_not_fall_through_to_provider():
    identifier = parse_identifier("corpusid:123")
    cache = FakeCache()
    cache.fail_get = True
    provider = FakeProvider({identifier: PaperLookupMatch([make_paper()])})
    lookup = PaperLookupService(runner(provider, cache=cache), pdf=make_pdf_service(), policies=make_policy_store())

    with pytest.raises(RedisConnectionError, match="cache unavailable"):
        await lookup.lookup(PaperLookup(["corpusid:123"]))

    assert provider.calls == []


@pytest.mark.asyncio
async def test_existing_common_paper_stops_common_chain_but_domain_provider_still_runs():
    identity = parse_identifier("2401.00001")
    offline = make_paper(
        doi="10.5555/example",
        metadata={
            "s2": {
                "citation_count": 5,
                "tldr": {"model": "release", "text": "known summary"},
            }
        },
    )
    unreachable = FakeProvider(error=AssertionError("The common paper is already acquired"))
    hf = FakeProvider(
        {
            identity: PaperLookupMatch(
                [
                    make_paper(
                        source=PaperSource.HUGGINGFACE,
                        metadata={"huggingface": {"upvotes": 0}},
                    )
                ]
            )
        }
    )
    run = runner(
        unreachable,
        extra_bindings={
            PaperSource.ARXIV: LookupBinding(FakeProvider(), None, replace(PROVIDER_POLICIES["arxiv"], identifier_kinds=[IdentifierKind.ARXIV])),
            PaperSource.HUGGINGFACE: LookupBinding(hf, None, replace(PROVIDER_POLICIES["huggingface"], identifier_kinds=[IdentifierKind.ARXIV])),
        },
    )
    target = run.target(identity, offline)

    await run.run(
        [target],
        common_sources=[PaperSource.ARXIV, PaperSource.S2],
        domain_sources=[PaperSource.HUGGINGFACE],
    )

    assert unreachable.calls == []
    assert len(hf.calls) == 1
    assert target.metadata["s2"]["citation_count"] == 5
    assert target.metadata["huggingface"]["upvotes"] == 0
    assert offline.metadata == {
        "s2": {
            "citation_count": 5,
            "tldr": {"model": "release", "text": "known summary"},
        }
    }


@pytest.mark.asyncio
async def test_arxiv_match_without_s2_signals_does_not_query_other_common_sources():
    identity = parse_identifier("2401.00001")
    base = make_paper(metadata={"arxiv": {"comments": "Accepted at a conference"}})
    arxiv = FakeProvider({identity: PaperLookupMatch([base])})
    failure = ProviderError("S2 request timed out")
    s2 = FakeProvider(error=failure)
    run = LookupRunner(
        {
            PaperSource.ARXIV: LookupBinding(arxiv, None, replace(PROVIDER_POLICIES["arxiv"], identifier_kinds=[IdentifierKind.ARXIV])),
            PaperSource.S2: LookupBinding(s2, None, replace(PROVIDER_POLICIES["s2"], identifier_kinds=[IdentifierKind.ARXIV])),
        }
    )

    result = await PaperLookupService(run, pdf=make_pdf_service(), policies=make_policy_store()).lookup(
        PaperLookup([identity.value])
    )

    assert result.items[0].status == LookupStatus.FOUND
    assert result.items[0].paper.metadata["arxiv"]["comments"] == "Accepted at a conference"
    assert result.items[0].error is None
    assert s2.calls == []


@pytest.mark.asyncio
async def test_all_provider_failures_reraise_the_original_error_object():
    failure = ProviderError("S2 rate limited")
    provider = FakeProvider(error=failure)
    lookup = service(provider)

    with pytest.raises(ProviderError) as caught:
        await lookup.lookup(PaperLookup(["corpusid:123"]))

    assert caught.value is failure


@pytest.mark.asyncio
async def test_conflicting_exact_identity_keeps_base_and_does_not_cache_provider_paper():
    identity = parse_identifier("2401.00001")
    base = make_paper(source=PaperSource.ARXIV, doi="10.5555/original", metadata={"arxiv": {"comments": "Author notes"}})
    contradictory = make_paper(
        arxiv_id="2402.00002",
        doi="10.5555/different",
        metadata={"s2": {"corpus_id": "222", "citation_count": 9}},
    )
    cache = FakeCache()
    run = LookupRunner(
        {
            PaperSource.ARXIV: LookupBinding(FakeProvider({identity: PaperLookupMatch([base])}), None, replace(PROVIDER_POLICIES["arxiv"], identifier_kinds=[IdentifierKind.ARXIV])),
            PaperSource.S2: LookupBinding(FakeProvider({identity: PaperLookupMatch([contradictory])}), cache, replace(PROVIDER_POLICIES["s2"], identifier_kinds=[IdentifierKind.ARXIV], aliases=s2_aliases)),
        }
    )

    result = await PaperLookupService(
        run, pdf=make_pdf_service(),
        policies=make_policy_store({AcademicField.GENERAL: FieldPolicy([], [PaperSource.S2])}),
    ).lookup(PaperLookup([identity.value]))

    assert result.items[0].paper.arxiv_id == base.arxiv_id
    assert result.items[0].paper.doi == base.doi
    assert result.items[0].paper.metadata == {"arxiv": {"comments": "Author notes"}}
    assert "conflicts" in result.items[0].error
    assert cache.puts == []


@pytest.mark.asyncio
async def test_simultaneous_credentials_share_one_inflight_lookup():
    identity = parse_identifier("corpusid:123")
    provider = FakeProvider({identity: PaperLookupMatch([make_paper()])})
    provider.block = True
    lookup = service(provider)
    first = asyncio.create_task(
        lookup.lookup(
            PaperLookup(["corpusid:123"]),
            credentials={PaperSource.S2: "key-a"},
        )
    )
    await provider.entered.wait()
    second = asyncio.create_task(
        lookup.lookup(
            PaperLookup(["corpusid:123"]),
            credentials={PaperSource.S2: "key-b"},
        )
    )
    await asyncio.sleep(0)

    assert provider.calls == [([identity], "key-a")]
    provider.release.set()
    enriched, full = await asyncio.gather(first, second)

    assert enriched.items[0].paper.title == "Offline title"
    assert full.items[0].paper.title == "Offline title"
    assert provider.cancelled.is_set() is False


@pytest.mark.asyncio
async def test_cancelling_one_shared_waiter_keeps_provider_alive_for_the_other():
    identity = parse_identifier("corpusid:123")
    provider = FakeProvider({identity: PaperLookupMatch([make_paper()])})
    provider.block = True
    lookup = service(provider)
    first = asyncio.create_task(
        lookup.lookup(PaperLookup(["corpusid:123"]))
    )
    await provider.entered.wait()
    second = asyncio.create_task(
        lookup.lookup(PaperLookup(["corpusid:123"]))
    )
    await asyncio.sleep(0)
    first.cancel()

    with pytest.raises(asyncio.CancelledError):
        await first
    assert provider.cancelled.is_set() is False
    provider.release.set()
    result = await second
    assert result.items[0].paper.title == "Offline title"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_cancelling_last_waiter_cancels_provider_request():
    provider = FakeProvider()
    provider.block = True
    lookup = service(provider)
    request = asyncio.create_task(
        lookup.lookup(PaperLookup(["corpusid:123"]))
    )
    await provider.entered.wait()
    request.cancel()

    with pytest.raises(asyncio.CancelledError):
        await request
    await asyncio.wait_for(provider.cancelled.wait(), timeout=1)


@pytest.mark.asyncio
async def test_programming_errors_propagate_without_provider_fallback():
    failure = TypeError("provider implementation bug")
    provider = FakeProvider(error=failure)

    with pytest.raises(TypeError) as caught:
        await service(provider).lookup(
            PaperLookup(["corpusid:123"])
        )

    assert caught.value is failure
