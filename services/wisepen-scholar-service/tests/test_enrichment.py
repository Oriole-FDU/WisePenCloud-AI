"""经 lookup 公共入口验证 S2 富化与共享缓存合同。"""

import asyncio
from dataclasses import replace
from datetime import date

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from scholar.application.enrichment import EnrichmentBinding, EnrichmentRunner
from scholar.application.lookup import (
    EnrichmentStatus,
    LookupPurpose,
    LookupStatus,
    PaperLookup,
    PaperLookupService,
)
from scholar.core.storage.redis.paper_cache import RedisPaperEnrichmentCache
from scholar.domain.interfaces.paper_enricher import PaperEnrichment
from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.identifiers import IdentifierKind, parse_identifier
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.repositories.paper_enrichment_cache import (
    CachedPaperEnrichment,
)
from scholar.domain.repositories.paper_repository import PaperLookupMatch
from scholar.providers.enrichment_rules import (
    arxiv_signals,
    s2_aliases,
    s2_short_lived,
    s2_signals,
)


class FakePaperRepository:
    def __init__(self, matches=None):
        self.matches = matches or {}
        self.calls = []

    async def lookup(self, identifiers):
        self.calls.append(list(identifiers))
        return [
            self.matches.get(identifier, PaperLookupMatch([]))
            for identifier in identifiers
        ]


class FakeCache:
    """按公共缓存端口模拟正负记录和 Redis 边界故障。"""

    def __init__(self):
        self.values = {}
        self.puts = []
        self.gets = []
        self.fail_get = False

    async def get(self, identifiers):
        self.gets.append(list(identifiers))
        if self.fail_get:
            raise RedisConnectionError("cache unavailable")
        return next(
            (
                self.values[identifier]
                for identifier in identifiers
                if identifier in self.values
            ),
            None,
        )

    async def put(self, identifiers, value, *, ttl_seconds):
        self.puts.append((list(identifiers), value, ttl_seconds))
        for identifier in identifiers:
            self.values[identifier] = value


class FakeEnricher:
    """实现 PaperEnricher 协议，可控制响应、外部失败和请求生命周期。"""

    def __init__(self, responses=None, *, error=None):
        self.responses = responses or {}
        self.error = error
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.block = False

    async def enrich(self, identifiers, *, include_details=False, api_key=None):
        identifiers = list(identifiers)
        self.calls.append((identifiers, include_details))
        self.entered.set()
        try:
            if self.block:
                await self.release.wait()
            if self.error is not None:
                raise self.error
            values = [self.responses.get(identifier) for identifier in identifiers]
            if not include_details:
                return [
                    None if value is None else replace(value, paper=None)
                    for value in values
                ]
            return values
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class FakeRedisPipeline:
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

    async def mget(self, keys):
        return [self.values.get(key) for key in keys]

    def pipeline(self, *, transaction):
        return FakeRedisPipeline(self)


def make_paper(
    arxiv_id="2401.00001", *, doi=None, source=PaperSource.ARXIV, metadata=None
):
    return Paper(
        arxiv_id=arxiv_id,
        doi=doi,
        title="Offline title",
        abstract="Offline abstract",
        authors=["Offline author"],
        publication_date=date(2024, 1, 2),
        year=2024,
        url="https://example.test/paper",
        pdf_url=None,
        source=source,
        metadata={} if metadata is None else metadata,
    )


def s2_enrichment(
    *,
    arxiv_id="2401.00001",
    doi=None,
    s2_id="a" * 40,
    corpus_id=123,
    citation_count=20,
    influential_count=3,
    tldr_text="A short contribution.",
    paper=True,
):
    identifiers = [parse_identifier(f"s2:{s2_id}")]
    if arxiv_id is not None:
        identifiers.append(parse_identifier(arxiv_id))
    if doi is not None:
        identifiers.append(parse_identifier(doi))
    if corpus_id is not None:
        identifiers.append(parse_identifier(f"corpusid:{corpus_id}"))
    metadata = {
        "s2": {
            "corpus_id": None if corpus_id is None else str(corpus_id),
            "paper_id": s2_id,
            "citation_count": citation_count,
            "influential_citation_count": influential_count,
            "is_open_access": False,
            "tldr": {"model": "tldr@v2", "text": tldr_text},
            "open_access_pdf": "https://example.test/paper.pdf",
            "bibtex": "@article{example}",
            "observed_at": "2026-10-27T00:00:00+00:00",
        }
    }
    full_paper = (
        make_paper(
            arxiv_id,
            doi=doi,
            source=PaperSource.S2,
            metadata=metadata,
        )
        if paper
        else None
    )
    return PaperEnrichment(identifiers, metadata, full_paper)


def service(*, repository=None, enricher=None, cache=None):
    return PaperLookupService(
        repository or FakePaperRepository(),
        enrichment=EnrichmentRunner(
            {
                "arxiv": EnrichmentBinding(
                    None,
                    None,
                    [IdentifierKind.ARXIV, IdentifierKind.DOI],
                    has_signals=arxiv_signals,
                ),
                "s2": EnrichmentBinding(
                    enricher,
                    cache,
                    [
                        IdentifierKind.ARXIV,
                        IdentifierKind.DOI,
                        IdentifierKind.S2,
                        IdentifierKind.CORPUS_ID,
                    ],
                    has_signals=s2_signals,
                    aliases=s2_aliases,
                    short_lived=s2_short_lived,
                ),
            }
        ),
    )


@pytest.mark.asyncio
async def test_redis_adapter_roundtrips_public_aliases_and_ttl():
    redis = FakeRedisBoundary()
    cache = RedisPaperEnrichmentCache(redis, "public-papers")
    arxiv = parse_identifier("2401.00001")
    doi = parse_identifier("10.5555/example")
    value = CachedPaperEnrichment(
        enrichment=PaperEnrichment(
            identifiers=[arxiv, doi],
            metadata={"s2": {"citation_count": 0, "tldr": {"text": ""}}},
        ),
        cache_aliases=(arxiv, doi),
        record_found=True,
        details_confirmed=False,
    )

    await cache.put([arxiv, doi], value, ttl_seconds=3600)

    assert await cache.get([doi]) == value
    assert set(redis.ttls.values()) == {3600}


@pytest.mark.asyncio
async def test_details_reads_complete_cached_paper_without_calling_s2():
    s2_id = parse_identifier("s2:" + "a" * 40)
    arxiv = parse_identifier("2401.00001")
    cached = s2_enrichment()
    cache = FakeCache()
    await cache.put(
        [s2_id, arxiv, *cached.identifiers],
        CachedPaperEnrichment(
            cached,
            tuple(dict.fromkeys([s2_id, arxiv, *cached.identifiers])),
            record_found=True,
            details_confirmed=True,
        ),
        ttl_seconds=86400,
    )
    local = make_paper(metadata={"arxiv": {"release": "snapshot-a"}})
    repository = FakePaperRepository({arxiv: PaperLookupMatch([local])})
    enricher = FakeEnricher()

    result = await service(
        repository=repository, enricher=enricher, cache=cache
    ).lookup(PaperLookup(["s2:" + "a" * 40, "arxiv:2401.00001"]))

    assert [item.status for item in result.items] == [
        LookupStatus.FOUND,
        LookupStatus.FOUND,
    ]
    assert result.items[0].paper == cached.paper
    assert result.items[1].paper.metadata["s2"]["citation_count"] == 20
    assert result.items[1].paper.metadata["arxiv"]["release"] == "snapshot-a"
    assert enricher.calls == []


@pytest.mark.asyncio
async def test_details_cache_miss_does_not_call_s2_or_claim_a_paper():
    enricher = FakeEnricher()
    result = await service(enricher=enricher, cache=FakeCache()).lookup(
        PaperLookup(["corpusid:123"])
    )

    assert result.items[0].status == LookupStatus.NOT_FOUND
    assert result.items[0].paper is None
    assert enricher.calls == []


@pytest.mark.asyncio
async def test_known_zero_false_and_empty_tldr_satisfy_core_offline():
    paper = make_paper(
        metadata={
            "s2": {
                "citation_count": 0,
                "influential_citation_count": 0,
                "is_open_access": False,
                "tldr": {"model": "offline-model", "text": ""},
                "release": "s2-release-1",
            }
        }
    )
    arxiv = parse_identifier("2401.00001")
    repository = FakePaperRepository({arxiv: PaperLookupMatch([paper])})
    enricher = FakeEnricher()

    result = await service(repository=repository, enricher=enricher).lookup(
        PaperLookup(["2401.00001"], purpose=LookupPurpose.ENRICH)
    )

    assert result.items[0].status == LookupStatus.FOUND
    assert result.items[0].paper is None
    assert result.items[0].metadata["s2"]["release"] == "s2-release-1"
    assert result.items[0].enrichment_status == EnrichmentStatus.COMPLETE
    assert enricher.calls == []


@pytest.mark.asyncio
async def test_observed_at_alone_does_not_suppress_missing_core_lookup():
    arxiv = parse_identifier("2401.00001")
    response = s2_enrichment()
    paper = make_paper(metadata={"s2": {"observed_at": "2026-10-27"}})
    repository = FakePaperRepository({arxiv: PaperLookupMatch([paper])})
    enricher = FakeEnricher({arxiv: response})

    result = await service(repository=repository, enricher=enricher).lookup(
        PaperLookup([arxiv.value], purpose=LookupPurpose.ENRICH)
    )

    assert result.items[0].status == LookupStatus.FOUND
    assert result.items[0].metadata["s2"]["citation_count"] == 20
    assert enricher.calls == [([arxiv], False)]


@pytest.mark.asyncio
async def test_existing_offline_signal_stops_common_chain_without_field_completion():
    arxiv = parse_identifier("2401.00001")
    offline = {
        "corpus_id": "123",
        "citation_count": 5,
        "influential_citation_count": None,
        "is_open_access": None,
        "tldr": {"model": "release-model", "text": "release summary"},
        "release": "release-2026-01",
        "paper_source": {"path": "papers/part-1.gz", "line": 7},
    }
    paper = make_paper(doi="10.5555/example", metadata={"s2": offline})
    repository = FakePaperRepository({arxiv: PaperLookupMatch([paper])})
    cache = FakeCache()
    enricher = FakeEnricher(
        {arxiv: s2_enrichment(doi="10.5555/example", citation_count=99)}
    )

    result = await service(
        repository=repository, enricher=enricher, cache=cache
    ).lookup(PaperLookup([arxiv.value], purpose=LookupPurpose.ENRICH))

    metadata = result.items[0].metadata["s2"]
    assert metadata["citation_count"] == 5
    assert metadata == offline
    assert result.items[0].paper is None
    assert enricher.calls == []
    assert cache.puts == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_ttl", "expected_status"),
    [
        (None, 3600, LookupStatus.NOT_FOUND),
        (
            s2_enrichment(citation_count=0, influential_count=None),
            3600,
            LookupStatus.FOUND,
        ),
        (
            s2_enrichment(citation_count=0, influential_count=0, tldr_text=""),
            3600,
            LookupStatus.FOUND,
        ),
    ],
)
async def test_negative_cache_suppresses_repeat_request_for_confirmed_absence_or_empty(
    response, expected_ttl, expected_status
):
    identifier = parse_identifier("corpusid:123")
    cache = FakeCache()
    enricher = FakeEnricher({identifier: response})
    lookup = service(enricher=enricher, cache=cache)

    first = await lookup.lookup(
        PaperLookup(["corpusid:123"], purpose=LookupPurpose.ENRICH)
    )
    second = await lookup.lookup(
        PaperLookup(["corpusid:123"], purpose=LookupPurpose.ENRICH)
    )

    assert first.items[0].status == second.items[0].status == expected_status
    assert len(enricher.calls) == 1
    assert cache.puts[0][2] == expected_ttl
    assert second.execution[-1].cache_hit is True


@pytest.mark.asyncio
async def test_full_uses_details_only_without_a_base_and_enrich_reuses_full_cache():
    s2_id = parse_identifier("s2:" + "a" * 40)
    enricher = FakeEnricher({s2_id: s2_enrichment()})
    cache = FakeCache()
    lookup = service(enricher=enricher, cache=cache)

    full = await lookup.lookup(
        PaperLookup(["s2:" + "a" * 40], purpose=LookupPurpose.FULL)
    )
    enrich = await lookup.lookup(
        PaperLookup(["s2:" + "a" * 40], purpose=LookupPurpose.ENRICH)
    )

    assert full.items[0].paper.source == PaperSource.S2
    assert full.items[0].metadata["s2"]["citation_count"] == 20
    assert enrich.items[0].paper is None
    assert enrich.items[0].metadata["s2"]["citation_count"] == 20
    assert enricher.calls == [([s2_id], True)]
    assert enrich.execution[-1].cache_hit is True


@pytest.mark.asyncio
async def test_full_with_base_does_not_request_s2_basic_paper_details():
    arxiv = parse_identifier("2401.00001")
    paper = make_paper(metadata={"s2": {"citation_count": None}})
    repository = FakePaperRepository({arxiv: PaperLookupMatch([paper])})
    enricher = FakeEnricher({arxiv: s2_enrichment()})

    result = await service(repository=repository, enricher=enricher).lookup(
        PaperLookup([arxiv.value], purpose=LookupPurpose.FULL)
    )

    assert result.items[0].paper.title == "Offline title"
    assert result.items[0].paper.metadata["s2"]["citation_count"] == 20
    assert enricher.calls == [([arxiv], False)]


@pytest.mark.asyncio
async def test_provider_failure_preserves_existing_offline_signals():
    complete = make_paper(
        "2401.00001",
        metadata={
            "s2": {
                "citation_count": 0,
                "influential_citation_count": 1,
                "tldr": {"model": "offline", "text": "known"},
            }
        },
    )
    incomplete = make_paper(
        "2402.00002",
        metadata={"s2": {"citation_count": 5, "influential_citation_count": None}},
    )
    first_id = parse_identifier("2401.00001")
    second_id = parse_identifier("2402.00002")
    repository = FakePaperRepository(
        {
            first_id: PaperLookupMatch([complete]),
            second_id: PaperLookupMatch([incomplete]),
        }
    )
    failure = ProviderError("S2 request timed out")
    enricher = FakeEnricher(error=failure)

    result = await service(repository=repository, enricher=enricher).lookup(
        PaperLookup([first_id.value, second_id.value], purpose=LookupPurpose.ENRICH)
    )

    assert [item.status for item in result.items] == [
        LookupStatus.FOUND,
        LookupStatus.FOUND,
    ]
    assert result.items[0].paper is None
    assert result.items[0].metadata["s2"]["citation_count"] == 0
    assert result.items[1].paper is None
    assert result.items[1].metadata["s2"]["citation_count"] == 5
    assert result.items[1].enrichment_status == EnrichmentStatus.COMPLETE
    assert result.items[1].error is None
    assert enricher.calls == []


@pytest.mark.asyncio
async def test_enrich_with_only_base_paper_and_provider_failure_reraises():
    arxiv = parse_identifier("2401.00001")
    repository = FakePaperRepository(
        {arxiv: PaperLookupMatch([make_paper(metadata={"arxiv": {"release": "r1"}})])}
    )
    failure = ProviderError("S2 temporarily unavailable")

    with pytest.raises(ProviderError) as caught:
        await service(
            repository=repository,
            enricher=FakeEnricher(error=failure),
            cache=FakeCache(),
        ).lookup(PaperLookup([arxiv.value], purpose=LookupPurpose.ENRICH))

    assert caught.value is failure


@pytest.mark.asyncio
async def test_all_no_base_provider_failures_reraise_original_error():
    failure = ProviderError("S2 rate limited")
    enricher = FakeEnricher(error=failure)

    with pytest.raises(ProviderError) as caught:
        await service(enricher=enricher).lookup(
            PaperLookup(["s2:" + "a" * 40], purpose=LookupPurpose.ENRICH)
        )

    assert caught.value is failure


@pytest.mark.asyncio
async def test_identity_conflict_keeps_base_and_does_not_write_aliases():
    doi = parse_identifier("10.5555/example")
    paper = make_paper(doi=doi.value, metadata={"s2": {"corpus_id": "111"}})
    repository = FakePaperRepository({doi: PaperLookupMatch([paper])})
    cache = FakeCache()
    response = s2_enrichment(
        arxiv_id="2402.00002",
        doi=doi.value,
        corpus_id=222,
    )
    enricher = FakeEnricher({parse_identifier(paper.arxiv_id): response})

    result = await service(
        repository=repository, enricher=enricher, cache=cache
    ).lookup(PaperLookup([doi.value], purpose=LookupPurpose.FULL))

    assert result.items[0].status == LookupStatus.FOUND
    assert result.items[0].paper.arxiv_id == paper.arxiv_id
    assert result.items[0].metadata["s2"] == {"corpus_id": "111"}
    assert "conflicts" in result.items[0].error
    assert cache.puts == []


@pytest.mark.asyncio
async def test_identical_lookup_requests_share_one_inflight_provider_call():
    identifier = parse_identifier("corpusid:123")
    enricher = FakeEnricher({identifier: s2_enrichment()})
    enricher.block = True
    lookup = service(enricher=enricher, cache=FakeCache())
    query = PaperLookup(["corpusid:123"], purpose=LookupPurpose.ENRICH)
    first = asyncio.create_task(lookup.lookup(query))
    await enricher.entered.wait()
    second = asyncio.create_task(lookup.lookup(query))
    await asyncio.sleep(0)
    assert len(enricher.calls) == 1

    enricher.release.set()
    first_result, second_result = await asyncio.gather(first, second)
    assert first_result.items[0].metadata == second_result.items[0].metadata
    assert len(enricher.calls) == 1


@pytest.mark.asyncio
async def test_concurrent_lookups_revalidate_distinct_offline_s2_identities():
    arxiv = parse_identifier("2401.00001")
    offline_s2_ids = ("a" * 40, "b" * 40)

    class SnapshotRepository:
        def __init__(self):
            self.read_count = 0
            self.second_read = asyncio.Event()

        async def lookup(self, identifiers):
            self.read_count += 1
            paper_id = offline_s2_ids[self.read_count - 1]
            if self.read_count == 2:
                self.second_read.set()
            paper = make_paper(
                arxiv_id=arxiv.value,
                metadata={"s2": {"paper_id": paper_id, "corpus_id": "123"}},
            )
            return [PaperLookupMatch([paper])]

    repository = SnapshotRepository()
    enricher = FakeEnricher(
        {arxiv: s2_enrichment(s2_id=offline_s2_ids[0], citation_count=20)}
    )
    enricher.block = True
    lookup = service(repository=repository, enricher=enricher, cache=FakeCache())

    first = asyncio.create_task(
        lookup.lookup(PaperLookup([arxiv.value], purpose=LookupPurpose.FULL))
    )
    await enricher.entered.wait()
    second = asyncio.create_task(
        lookup.lookup(PaperLookup([arxiv.value], purpose=LookupPurpose.FULL))
    )
    await repository.second_read.wait()
    await asyncio.sleep(0)
    enricher.release.set()

    first_result, second_result = await asyncio.gather(first, second)

    assert first_result.items[0].enrichment_status == EnrichmentStatus.COMPLETE
    assert second_result.items[0].enrichment_status == EnrichmentStatus.PARTIAL
    assert "conflicts" in second_result.items[0].error
    assert second_result.items[0].metadata["s2"]["paper_id"] == offline_s2_ids[1]
    assert "citation_count" not in second_result.items[0].metadata["s2"]


@pytest.mark.asyncio
async def test_last_cancelled_lookup_waiter_cancels_provider_call():
    enricher = FakeEnricher()
    enricher.block = True
    lookup = service(enricher=enricher, cache=FakeCache())
    request = asyncio.create_task(
        lookup.lookup(PaperLookup(["corpusid:123"], purpose=LookupPurpose.ENRICH))
    )
    await enricher.entered.wait()
    request.cancel()

    with pytest.raises(asyncio.CancelledError):
        await request
    await asyncio.wait_for(enricher.cancelled.wait(), timeout=1)
