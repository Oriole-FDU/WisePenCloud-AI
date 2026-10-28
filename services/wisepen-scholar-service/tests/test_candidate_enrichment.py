from scholar.domain.interfaces.paper_searcher import PaperSearchBatch

"""从公开搜索用例验证候选富化，并验证共享事实缓存的 alias 失效。"""

import asyncio
from datetime import date

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from scholar.application.enrichment import EnrichmentBinding, EnrichmentRunner
from scholar.application.field_policies import FieldPolicy
from scholar.application.search import (
    PaperSearch,
    PaperSearchService,
    SearchBinding,
)
from scholar.application.source_execution import SourceExecutionStatus
from scholar.core.storage.redis.paper_cache import RedisPaperEnrichmentCache
from scholar.domain.interfaces.paper_enricher import PaperEnrichment
from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import IdentifierKind, parse_identifier
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.repositories.paper_enrichment_cache import (
    CachedPaperEnrichment,
)
from scholar.domain.repositories.paper_repository import PaperLookupMatch
from scholar.providers.arxiv import ArxivProvider
from scholar.providers.huggingface import HuggingFaceProvider


def make_paper(
    arxiv_id="2401.00001",
    *,
    doi=None,
    source=PaperSource.OPENALEX,
    metadata=None,
):
    return Paper(
        arxiv_id=arxiv_id,
        doi=doi,
        title="Candidate title",
        abstract="Candidate abstract",
        authors=["Candidate author"],
        publication_date=date(2024, 1, 2),
        year=2024,
        url="https://example.test/paper",
        pdf_url=None,
        source=source,
        metadata={} if metadata is None else metadata,
    )


class FakeQueryCache:
    """隔离基础召回缓存，不绑定检索用例生成的 key。"""

    async def get(self, key):
        return None

    async def put(self, key, papers):
        pass


class FakeSearcher:
    def __init__(self, papers):
        self.papers = papers
        self.calls = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        return PaperSearchBatch(list(self.papers))


class FailingSearcher:
    def __init__(self, error):
        self.error = error

    async def search(self, **kwargs):
        raise self.error


class FakeCache:
    """模拟共享缓存端口，不约束生产键格式。"""

    def __init__(self):
        self.values = {}
        self.puts = []
        self.get_calls = []
        self.fail_get = False

    async def get(self, identifiers):
        self.get_calls.append(list(identifiers))
        if self.fail_get:
            raise RedisConnectionError("redis unavailable")
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
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    async def enrich(self, identifiers, *, include_details=False, api_key=None):
        self.calls.append((list(identifiers), include_details))
        return [self.outcomes[identifier] for identifier in identifiers]


class FakePaperRepository:
    def __init__(self, papers):
        self.papers = papers
        self.calls = []

    async def lookup(self, identifiers):
        self.calls.append(list(identifiers))
        return [
            PaperLookupMatch(
                [self.papers[identifier]] if identifier in self.papers else []
            )
            for identifier in identifiers
        ]


def search_service(papers, bindings, sources):
    searcher = FakeSearcher(papers)
    service = PaperSearchService(
        searchers={"base": SearchBinding(searcher)},
        policies={
            AcademicField.GENERAL: FieldPolicy(
                searchers=["base"], field_enrichers=list(sources)
            )
        },
        query_cache=FakeQueryCache(),
        enrichment=EnrichmentRunner(bindings),
    )
    return service, searcher


async def search(service, query="candidate test"):
    return await service.search(PaperSearch(query, field=AcademicField.GENERAL))


def hf_payload(arxiv_id):
    return {
        "id": arxiv_id,
        "title": "HF title",
        "publishedAt": "2024-01-02T00:00:00Z",
        "submittedOnDailyAt": "2024-01-01T00:00:00Z",
        "upvotes": 0,
        "githubRepo": "",
        "githubStars": False,
        "linkedModels": [{"id": "org/model", "likes": 0}],
        "numTotalModels": 1,
        "linkedDatasets": [],
        "numTotalDatasets": None,
        "paper": {
            "id": arxiv_id,
            "publishedAt": "2024-01-02T00:00:00Z",
            "summary": "HF abstract",
            "authors": [{"name": "HF author"}],
        },
    }


@pytest.mark.asyncio
async def test_configured_hf_enrichment_preserves_order_values_and_caches_404():
    requests = []

    async def handle(request):
        requests.append(request)
        arxiv_id = request.url.path.rsplit("/", 1)[-1]
        if arxiv_id == "2401.00003":
            return httpx.Response(404)
        payload = hf_payload(arxiv_id)
        if arxiv_id == "2401.00002":
            payload["githubRepo"] = "new/repository"
            payload["githubStars"] = True
            payload["linkedDatasets"] = [{"id": "new/dataset", "likes": 1}]
        return httpx.Response(200, json=payload)

    first = make_paper(doi="10.5555/first")
    second = make_paper(
        "2401.00002",
        metadata={
            "huggingface": {
                "upvotes": 12,
                "linked_models": [{"id": "existing/model", "likes": 3}],
                "github_repo": "",
                "github_stars": False,
                "linked_datasets": [],
                "num_total_models": 0,
            }
        },
    )
    missing = make_paper("2401.00003")
    doi_only = make_paper(None, doi="10.5555/no-arxiv")
    papers = [first, second, missing, doi_only]
    cache = FakeCache()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        provider = HuggingFaceProvider(http)
        service, _ = search_service(
            papers,
            {
                "huggingface": EnrichmentBinding(
                    enricher=provider,
                    cache=cache,
                    identifier_kinds=[IdentifierKind.ARXIV],
                )
            },
            ["huggingface"],
        )
        result = await search(service)
        repeated = await search(service)

    assert len(result.papers) == len(papers)
    assert [paper.arxiv_id for paper in result.papers] == [
        "2401.00001",
        "2401.00002",
        "2401.00003",
        None,
    ]
    assert result.papers[0].source == PaperSource.OPENALEX
    assert result.papers[0].title == "Candidate title"
    assert result.papers[0].metadata["huggingface"]["upvotes"] == 0
    assert result.papers[0].metadata["huggingface"]["github_stars"] is False
    assert result.papers[0].metadata["huggingface"]["linked_models"] == [
        {"id": "org/model", "likes": 0}
    ]
    assert result.papers[0].metadata["huggingface"]["linked_datasets"] == []
    assert result.papers[0].metadata["huggingface"]["num_total_datasets"] is None
    assert result.papers[1].metadata["huggingface"]["upvotes"] == 0
    assert result.papers[1].metadata["huggingface"]["github_repo"] == "new/repository"
    assert result.papers[1].metadata["huggingface"]["linked_models"] == [
        {"id": "org/model", "likes": 0}
    ]
    assert first.metadata == {}
    assert second.metadata["huggingface"]["upvotes"] == 12
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        "2401.00001",
        "2401.00002",
        "2401.00003",
    ]
    assert [value[2] for value in cache.puts] == [
        24 * 60 * 60,
        24 * 60 * 60,
        60 * 60,
    ]
    assert result.enrichment_execution[0].status == SourceExecutionStatus.COMPLETED

    assert repeated.enrichment_execution[0].cache_hit is True
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_cached_source_only_merges_known_records_and_does_not_fetch_misses():
    arxiv_id = parse_identifier("2401.00001")
    missing_id = parse_identifier("2401.00002")
    cache = FakeCache()
    cache.values[arxiv_id] = CachedPaperEnrichment(
        enrichment=PaperEnrichment(
            identifiers=[arxiv_id],
            metadata={
                "signals": {
                    "citation_count": 0,
                    "tldr": {"model": "online", "text": "new summary"},
                }
            },
        ),
        cache_aliases=(arxiv_id,),
        record_found=True,
        details_confirmed=False,
    )
    first = make_paper(
        metadata={
            "signals": {
                "citation_count": None,
                "tldr": {"model": "offline", "text": ""},
            }
        }
    )
    missing = make_paper("2401.00002")
    service, _ = search_service(
        [first, missing],
        {
            "signals": EnrichmentBinding(
                enricher=None,
                cache=cache,
                identifier_kinds=[IdentifierKind.ARXIV],
            )
        },
        ["signals"],
    )

    result = await search(service)

    assert result.papers[0].metadata["signals"]["citation_count"] == 0
    assert result.papers[0].metadata["signals"]["tldr"] == {
        "model": "online",
        "text": "new summary",
    }
    assert result.papers[1].metadata == {}
    assert result.enrichment_execution[0].status == SourceExecutionStatus.PARTIAL
    assert result.enrichment_execution[0].cache_hit is True
    assert cache.get_calls == [[arxiv_id], [missing_id]]


@pytest.mark.asyncio
async def test_arxiv_enrichment_batches_repository_reads_and_projects_its_namespace():
    first_id = parse_identifier("2401.00001")
    second_id = parse_identifier("2401.00002")
    repository = FakePaperRepository(
        {
            first_id: make_paper(
                "2401.00001",
                source=PaperSource.ARXIV,
                metadata={
                    "arxiv": {"license": "MIT", "categories": ["cs.AI"]},
                    "s2": {"citation_count": 0},
                },
            ),
            second_id: make_paper(
                "2401.00002",
                source=PaperSource.ARXIV,
                metadata={"arxiv": {"license": "CC-BY"}},
            ),
        }
    )
    candidates = [
        make_paper("2401.00001"),
        make_paper("2401.00002"),
        make_paper("2401.00001"),
    ]
    service, _ = search_service(
        candidates,
        {
            "arxiv": EnrichmentBinding(
                enricher=ArxivProvider(repository, embedding_client=None),
                cache=None,
                identifier_kinds=[IdentifierKind.ARXIV],
            )
        },
        ["arxiv"],
    )

    result = await search(service)

    assert repository.calls == [[first_id, second_id]]
    assert [paper.metadata["arxiv"]["license"] for paper in result.papers] == [
        "MIT",
        "CC-BY",
        "MIT",
    ]
    assert result.papers[0].title == candidates[0].title
    assert result.papers[0].source == PaperSource.OPENALEX
    assert result.papers[0].metadata["s2"]["citation_count"] == 0
    assert result.enrichment_execution[0].cache_hit is False
    assert result.enrichment_execution[0].status == SourceExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_http_failure_preserves_candidates_and_is_not_negative_cached():
    async def handle(request):
        return httpx.Response(503)

    paper = make_paper()
    cache = FakeCache()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        service, _ = search_service(
            [paper],
            {
                "huggingface": EnrichmentBinding(
                    enricher=HuggingFaceProvider(http),
                    cache=cache,
                    identifier_kinds=[IdentifierKind.ARXIV],
                )
            },
            ["huggingface"],
        )
        result = await search(service)

    execution = result.enrichment_execution[0]
    assert result.papers[0].arxiv_id == paper.arxiv_id
    assert result.papers[0].metadata == paper.metadata
    assert execution.status == SourceExecutionStatus.FAILED
    assert "503" in execution.error
    assert cache.puts == []


@pytest.mark.asyncio
async def test_optional_enrichment_provider_error_preserves_base_candidate():
    paper = make_paper()

    class FailedEnricher:
        async def enrich(self, identifiers, *, include_details=False, api_key=None):
            raise ProviderError("Hugging Face is unavailable")

    service, _ = search_service(
        [paper],
        {
            "huggingface": EnrichmentBinding(
                enricher=FailedEnricher(),
                cache=None,
                identifier_kinds=[IdentifierKind.ARXIV],
            )
        },
        ["huggingface"],
    )

    result = await search(service)

    assert result.papers[0].arxiv_id == paper.arxiv_id
    assert result.papers[0].metadata == paper.metadata
    assert result.enrichment_execution[0].status == SourceExecutionStatus.FAILED
    assert result.enrichment_execution[0].error == "Hugging Face is unavailable"


@pytest.mark.asyncio
async def test_search_propagates_single_source_provider_error():
    service = PaperSearchService(
        searchers={"base": SearchBinding(FailingSearcher(ProviderError("offline")))},
        policies={AcademicField.GENERAL: FieldPolicy(searchers=["base"])},
        query_cache=FakeQueryCache(),
        enrichment=EnrichmentRunner({}),
    )

    with pytest.raises(ProviderError, match="offline"):
        await search(service)


@pytest.mark.asyncio
async def test_search_propagates_error_when_all_multisource_providers_fail():
    service = PaperSearchService(
        searchers={
            "arxiv": SearchBinding(FailingSearcher(ProviderError("arXiv offline"))),
            "openalex": SearchBinding(
                FailingSearcher(ProviderError("OpenAlex offline"))
            ),
        },
        policies={
            AcademicField.GENERAL: FieldPolicy(
                searchers=["arxiv"],
            )
        },
        query_cache=FakeQueryCache(),
        enrichment=EnrichmentRunner({}),
    )

    with pytest.raises(ProviderError, match="arXiv offline"):
        await service.search(PaperSearch("candidate test", source_scope="multi_source"))


@pytest.mark.asyncio
async def test_conflicting_exact_identity_does_not_merge_or_write_aliases():
    arxiv = parse_identifier("2401.00001")
    paper = make_paper(doi="10.5555/original")
    conflicting = PaperEnrichment(
        identifiers=[arxiv, parse_identifier("10.5555/different")],
        metadata={"huggingface": {"upvotes": 9}},
    )
    cache = FakeCache()
    service, _ = search_service(
        [paper],
        {
            "huggingface": EnrichmentBinding(
                enricher=FakeEnricher({arxiv: conflicting}),
                cache=cache,
                identifier_kinds=[IdentifierKind.ARXIV],
            )
        },
        ["huggingface"],
    )

    result = await search(service)

    assert result.papers[0].metadata == paper.metadata
    assert result.enrichment_execution[0].status == SourceExecutionStatus.FAILED
    assert cache.puts == []


@pytest.mark.asyncio
async def test_cache_read_failure_does_not_bypass_cache_before_online_enrichment():
    arxiv = parse_identifier("2401.00001")
    paper = make_paper()
    cache = FakeCache()
    cache.fail_get = True
    enricher = FakeEnricher(
        {
            arxiv: PaperEnrichment(
                identifiers=[arxiv], metadata={"huggingface": {"upvotes": 1}}
            )
        }
    )
    service, _ = search_service(
        [paper],
        {
            "huggingface": EnrichmentBinding(
                enricher=enricher,
                cache=cache,
                identifier_kinds=[IdentifierKind.ARXIV],
            )
        },
        ["huggingface"],
    )

    result = await search(service)

    assert result.papers[0].metadata == paper.metadata
    assert enricher.calls == []
    assert result.enrichment_execution[0].status == SourceExecutionStatus.FAILED
    assert "redis unavailable" in result.enrichment_execution[0].error


@pytest.mark.asyncio
async def test_source_without_supported_candidate_identity_is_not_executed():
    cache = FakeCache()
    enricher = FakeEnricher({})
    service, _ = search_service(
        [make_paper(None, doi="10.5555/doi-only")],
        {
            "huggingface": EnrichmentBinding(
                enricher=enricher,
                cache=cache,
                identifier_kinds=[IdentifierKind.ARXIV],
            )
        },
        ["huggingface"],
    )

    result = await search(service)

    assert result.enrichment_execution[0].status == SourceExecutionStatus.NOT_EXECUTED
    assert cache.get_calls == []
    assert enricher.calls == []


@pytest.mark.asyncio
async def test_candidate_uses_arxiv_before_doi_when_both_are_supported():
    arxiv = parse_identifier("2401.00001")
    doi = parse_identifier("10.5555/candidate")
    enricher = FakeEnricher(
        {
            arxiv: PaperEnrichment(
                identifiers=[arxiv], metadata={"signals": {"value": "from-arxiv"}}
            )
        }
    )
    paper = make_paper(doi=doi.value)
    service, _ = search_service(
        [paper],
        {
            "signals": EnrichmentBinding(
                enricher=enricher,
                cache=None,
                identifier_kinds=[IdentifierKind.DOI, IdentifierKind.ARXIV],
            )
        },
        ["signals"],
    )

    result = await search(service)

    assert enricher.calls == [([arxiv], False)]
    assert result.papers[0].metadata["signals"]["value"] == "from-arxiv"


@pytest.mark.asyncio
async def test_confirmed_empty_fields_skip_reads_but_missing_field_uses_enricher():
    fields = [
        "upvotes",
        "github_repo",
        "github_stars",
        "linked_models",
        "num_total_models",
        "linked_datasets",
        "num_total_datasets",
    ]
    confirmed = make_paper(
        "2401.00001",
        metadata={
            "huggingface": {
                "upvotes": 0,
                "github_repo": "",
                "github_stars": False,
                "linked_models": [],
                "num_total_models": None,
                "linked_datasets": [],
                "num_total_datasets": None,
            }
        },
    )
    missing = make_paper(
        "2401.00002",
        metadata={
            "huggingface": {
                "upvotes": None,
                "github_repo": "",
                "github_stars": False,
                "linked_models": [],
                "num_total_models": 0,
                "num_total_datasets": None,
            }
        },
    )
    arxiv = parse_identifier("2401.00002")
    cache = FakeCache()
    enricher = FakeEnricher(
        {
            arxiv: PaperEnrichment(
                identifiers=[arxiv],
                metadata={"huggingface": {"linked_datasets": []}},
            )
        }
    )
    service, _ = search_service(
        [confirmed, missing],
        {
            "huggingface": EnrichmentBinding(
                enricher=enricher,
                cache=cache,
                identifier_kinds=[IdentifierKind.ARXIV],
                fields=fields,
            )
        },
        ["huggingface"],
    )

    result = await search(service)

    assert cache.get_calls == [[arxiv]]
    assert enricher.calls == [([arxiv], False)]
    assert result.papers[0].metadata["huggingface"]["upvotes"] == 0
    assert result.papers[0].metadata["huggingface"]["github_repo"] == ""
    assert result.papers[0].metadata["huggingface"]["linked_models"] == []
    assert result.papers[1].metadata["huggingface"]["linked_datasets"] == []


@pytest.mark.asyncio
async def test_search_reuses_identical_enrichment_across_concurrent_queries():
    class BlockingEnricher:
        def __init__(self):
            self.active = 0
            self.peak = 0
            self.calls = []
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def enrich(self, identifiers, *, include_details=False, api_key=None):
            self.calls.append(list(identifiers))
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.entered.set()
            try:
                await self.release.wait()
            finally:
                self.active -= 1
            return [
                PaperEnrichment(identifiers=[identifier], metadata={})
                for identifier in identifiers
            ]

    enricher = BlockingEnricher()
    service, _ = search_service(
        [make_paper("2401.00001")],
        {
            "hf": EnrichmentBinding(
                enricher=enricher,
                cache=None,
                identifier_kinds=[IdentifierKind.ARXIV],
            )
        },
        ["hf"],
    )
    first = asyncio.create_task(search(service, query="first"))
    await enricher.entered.wait()
    second = asyncio.create_task(search(service, query="second"))
    await asyncio.sleep(0)

    assert enricher.peak == 1
    assert len(enricher.calls) == 1
    enricher.release.set()
    await asyncio.gather(first, second)
    assert enricher.peak == 1
    assert len(enricher.calls) == 1


class FakeRedisPipeline:
    def __init__(self, redis):
        self.redis = redis
        self.pending = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def set(self, key, value, *, ex):
        self.pending[key] = value

    async def execute(self):
        self.redis.values.update(self.pending)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.fail_delete = False

    async def mget(self, keys):
        return [self.values.get(key) for key in keys]

    async def delete(self, *keys):
        if self.fail_delete:
            raise RedisConnectionError("redis unavailable")
        for key in keys:
            self.values.pop(key, None)

    def pipeline(self, *, transaction):
        return FakeRedisPipeline(self)


@pytest.mark.asyncio
async def test_cache_invalidation_expands_aliases_and_propagates_redis_failure():
    arxiv = parse_identifier("2401.00001")
    doi = parse_identifier("10.5555/shared")
    unrelated = parse_identifier("2402.00002")
    redis = FakeRedis()
    cache = RedisPaperEnrichmentCache(redis, "hf")
    shared = CachedPaperEnrichment(
        enrichment=PaperEnrichment(
            identifiers=[arxiv, doi], metadata={"huggingface": {"upvotes": 0}}
        ),
        cache_aliases=(arxiv, doi),
        record_found=True,
        details_confirmed=False,
    )
    other = CachedPaperEnrichment(
        enrichment=PaperEnrichment(
            identifiers=[unrelated], metadata={"huggingface": {"upvotes": 1}}
        ),
        cache_aliases=(unrelated,),
        record_found=True,
        details_confirmed=False,
    )
    await cache.put([arxiv, doi], shared, ttl_seconds=3600)
    await cache.put([unrelated], other, ttl_seconds=3600)

    await cache.invalidate([doi])

    assert await cache.get([arxiv]) is None
    assert await cache.get([doi]) is None
    assert await cache.get([unrelated]) == other
    redis.fail_delete = True
    with pytest.raises(RedisConnectionError):
        await cache.invalidate([unrelated])
