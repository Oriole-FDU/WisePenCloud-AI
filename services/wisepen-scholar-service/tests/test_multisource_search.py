from dataclasses import replace
from datetime import date

import pytest
from common.core.exceptions import ServiceException

from scholar.application.field_policies import FIELD_POLICIES, FieldPolicy
from scholar.application.search import (
    PaperSearch,
    PaperSearchService,
    SearchBinding,
    SearchResult,
    SourceScope,
)
from scholar.application.search.enrichment import EnrichmentBinding
from scholar.application.source_execution import (
    SourceExecutionStatus,
)
from scholar.domain.interfaces.paper_enricher import PaperEnrichment
from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import IdentifierKind
from scholar.domain.models.paper import Paper, PaperSource
from scholar.providers.arxiv import ArxivProvider
from scholar.providers.field_mapping import (
    ARXIV_CATEGORIES,
    OPENALEX_FILTERS,
)


class MemoryCache:
    def __init__(self):
        self.values = {}
        self.get_calls = []
        self.put_calls = []

    async def get(self, key):
        self.get_calls.append(key)
        return self.values.get(key)

    async def put(self, key, papers):
        self.put_calls.append((key, papers))
        self.values[key] = papers


class FakePaperRepository:
    def __init__(self, papers=None, error=None):
        self.papers = papers or []
        self.error = error
        self.calls = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.papers


class FakeSearcher:
    def __init__(self, search=None):
        self.search_result = search or []
        self.calls = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.search_result, Exception):
            raise self.search_result
        if callable(self.search_result):
            return self.search_result(kwargs)
        return self.search_result


def paper(
    source,
    *,
    arxiv_id=None,
    doi=None,
    title="A paper",
    metadata=None,
):
    return Paper(
        arxiv_id=arxiv_id,
        doi=doi,
        title=title,
        abstract=f"Abstract for {title}",
        authors=["A. Researcher"],
        publication_date=date(2024, 1, 2),
        year=2024,
        url=f"https://{source}.example/paper",
        pdf_url=None,
        source=source,
        metadata=metadata or {},
    )


def service(*, arxiv=None, openalex=None, cache=None):
    return PaperSearchService(
        searchers={
            "arxiv": SearchBinding(arxiv or FakeSearcher()),
            "openalex": SearchBinding(
                openalex or FakeSearcher(), requires_api_key=True
            ),
        },
        policies={
            field: replace(policy, search_enrichers=[])
            for field, policy in FIELD_POLICIES.items()
        },
        enrichment_bindings={},
        query_cache=cache or MemoryCache(),
    )


def test_all_openalex_domain_filters_and_default_routes_match_the_search_plan():
    assert OPENALEX_FILTERS == {
        AcademicField.GENERAL: None,
        AcademicField.COMPUTER_SCIENCE: "topics.field.id:17",
        AcademicField.MEDICINE: "topics.field.id:27|29|30|35|36",
        AcademicField.BIOLOGY: "topics.field.id:11|13|24|28|34",
        AcademicField.ENGINEERING: "topics.field.id:15|21|22",
        AcademicField.ECONOMICS_BUSINESS: "topics.field.id:14|20",
        AcademicField.MATHEMATICS: "topics.field.id:26|18",
        AcademicField.PHYSICS: "topics.field.id:31",
        AcademicField.CHEMISTRY: "topics.field.id:16",
        AcademicField.PSYCHOLOGY: "topics.field.id:32",
        AcademicField.SOCIAL_SCIENCES: "topics.field.id:33",
        AcademicField.ARTS_HUMANITIES: "topics.field.id:12",
        AcademicField.LAW: "topics.subfield.id:3308",
        AcademicField.MATERIALS_ENVIRONMENTAL_SCIENCES: "topics.field.id:19|23|25",
    }
    assert set(ARXIV_CATEGORIES) == set(AcademicField)


@pytest.mark.asyncio
async def test_default_routing_uses_mapped_openalex_or_preserves_local_arxiv_order():
    oa = FakeSearcher()
    arxiv_papers = [
        paper(PaperSource.ARXIV, arxiv_id="2401.00001", title="First"),
        paper(PaperSource.ARXIV, arxiv_id="2401.00002", title="Second"),
    ]
    arxiv = FakePaperRepository(arxiv_papers)
    cache = MemoryCache()
    search = service(arxiv=arxiv, openalex=oa, cache=cache)

    openalex_result = await search.search(
        PaperSearch("law", field=AcademicField.LAW),
        credentials={"openalex": "law-key"},
        user_id="user-1",
    )
    cs_result = await search.search(
        PaperSearch("transformers", field=AcademicField.COMPUTER_SCIENCE),
        credentials={"openalex": "cs-key"},
        user_id="user-1",
    )
    fallback_result = await search.search(PaperSearch("general terms"))

    assert oa.calls[0]["field"] == AcademicField.LAW
    assert oa.calls[0]["api_key"] == "law-key"
    assert len(oa.calls) == 1
    assert arxiv.calls[0]["field"] == AcademicField.COMPUTER_SCIENCE
    assert arxiv.calls[1]["field"] == AcademicField.GENERAL
    assert [item.title for item in cs_result.papers] == ["First", "Second"]
    assert [item.title for item in fallback_result.papers] == ["First", "Second"]
    assert cs_result.execution[0].source == PaperSource.ARXIV
    assert openalex_result.execution[0].source == PaperSource.OPENALEX


@pytest.mark.asyncio
async def test_arxiv_provider_maps_shared_field_and_does_not_forward_api_key():
    repository = FakePaperRepository()
    provider = ArxivProvider(repository)

    await provider.search(
        query="genomics",
        field=AcademicField.BIOLOGY,
        published_after=None,
        published_before=None,
        limit=5,
        api_key="ignored-by-arxiv",
    )

    assert repository.calls == [
        {
            "query": "genomics",
            "categories": ["q-bio.*"],
            "published_after": None,
            "published_before": None,
            "limit": 5,
        }
    ]


@pytest.mark.asyncio
async def test_multisource_missing_key_fails_before_cache_or_source_calls():
    arxiv = FakePaperRepository()
    openalex = FakeSearcher()
    cache = MemoryCache()
    search = service(arxiv=arxiv, openalex=openalex, cache=cache)

    with pytest.raises(ServiceException):
        await search.search(
            PaperSearch("question", source_scope=SourceScope.MULTI_SOURCE)
        )
    with pytest.raises(ServiceException):
        await search.search(
            PaperSearch(
                "question",
                field=AcademicField.LAW,
                source_scope=SourceScope.MULTI_SOURCE,
            ),
            credentials={"openalex": "law-key"},
            user_id="user-1",
        )

    assert cache.get_calls == []
    assert arxiv.calls == []
    assert openalex.calls == []


@pytest.mark.asyncio
async def test_openalex_cache_isolated_by_user_and_key_fingerprint():
    cache = MemoryCache()

    def result_for_key(arguments):
        title = {"key-a": "User A", "key-b": "User B"}[arguments["api_key"]]
        return [paper(PaperSource.OPENALEX, doi="10.1000/shared", title=title)]

    openalex = FakeSearcher(search=result_for_key)
    search = service(openalex=openalex, cache=cache)
    query = PaperSearch("same query")

    first_a = await search.search(
        query, credentials={"openalex": "key-a"}, user_id="user-a"
    )
    user_b = await search.search(
        query, credentials={"openalex": "key-b"}, user_id="user-a"
    )
    another_user = await search.search(
        query, credentials={"openalex": "key-a"}, user_id="user-b"
    )
    cached_a = await search.search(
        query, credentials={"openalex": "key-a"}, user_id="user-a"
    )

    assert first_a.papers[0].title == "User A"
    assert user_b.papers[0].title == "User B"
    assert another_user.papers[0].title == "User A"
    assert cached_a.papers[0].title == "User A"
    assert len(openalex.calls) == 3
    assert len(cache.put_calls) == 3
    assert len({key for key, _ in cache.put_calls}) == 3
    assert all("key-a" not in key and "key-b" not in key for key, _ in cache.put_calls)
    assert cached_a.execution[0].cache_hit


@pytest.mark.asyncio
async def test_openalex_cache_is_skipped_for_trusted_byok_without_user_id():
    openalex = FakeSearcher([paper(PaperSource.OPENALEX, doi="10.1000/trusted")])
    cache = MemoryCache()
    search = service(openalex=openalex, cache=cache)

    await search.search(
        PaperSearch("same query"), credentials={"openalex": "trusted-key"}
    )
    await search.search(
        PaperSearch("same query"), credentials={"openalex": "trusted-key"}
    )

    assert len(openalex.calls) == 2
    assert cache.get_calls == []
    assert cache.put_calls == []


@pytest.mark.asyncio
async def test_multisource_fuses_by_exact_ids_and_keeps_identifier_conflicts_visible():
    arxiv_papers = [
        paper(
            PaperSource.ARXIV,
            arxiv_id="1706.03762",
            doi="10.1000/arxiv-doi",
            title="arXiv facts",
            metadata={"arxiv": {"raw_id": "1706.03762"}},
        ),
        paper(
            PaperSource.ARXIV,
            arxiv_id="2310.00001",
            doi="10.1000/conflict",
            title="arXiv conflict",
        ),
        paper(
            PaperSource.ARXIV,
            arxiv_id="2310.00003",
            title="same title",
        ),
    ]
    openalex_papers = [
        paper(
            PaperSource.OPENALEX,
            arxiv_id="1706.03762",
            doi="10.1000/openalex-doi",
            title="OpenAlex facts",
            metadata={"openalex": {"id": "W1"}},
        ),
        paper(
            PaperSource.OPENALEX,
            arxiv_id="2310.99999",
            doi="10.1000/conflict",
            title="OpenAlex conflict",
        ),
        paper(
            PaperSource.OPENALEX,
            arxiv_id="2310.00004",
            title="same title",
        ),
    ]
    search = service(
        arxiv=FakePaperRepository(arxiv_papers),
        openalex=FakeSearcher(openalex_papers),
        cache=MemoryCache(),
    )

    result = await search.search(
        PaperSearch("question", source_scope=SourceScope.MULTI_SOURCE),
        credentials={"openalex": "user-key"},
        user_id="user-1",
    )

    assert isinstance(result, SearchResult)
    assert result.papers[0].title == "arXiv facts"
    assert result.papers[0].doi == "10.1000/arxiv-doi"
    assert result.papers[0].metadata == {
        "openalex": {"id": "W1"},
        "arxiv": {"raw_id": "1706.03762"},
    }
    assert len(result.papers) == 5
    assert len([paper for paper in result.papers if paper.title == "same title"]) == 2
    assert result.identity_conflicts
    assert result.execution[0].status == SourceExecutionStatus.COMPLETED
    assert result.execution[1].status == SourceExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_multisource_keeps_records_separate_when_one_doi_matches_many():
    shared_doi = "10.1000/shared"
    search = service(
        arxiv=FakePaperRepository(
            [
                paper(
                    PaperSource.ARXIV,
                    doi=shared_doi,
                    title="arXiv record one",
                ),
                paper(
                    PaperSource.ARXIV,
                    doi=shared_doi,
                    title="arXiv record two",
                ),
            ]
        ),
        openalex=FakeSearcher(
            [paper(PaperSource.OPENALEX, doi=shared_doi, title="OpenAlex record")]
        ),
    )

    result = await search.search(
        PaperSearch("shared DOI", source_scope=SourceScope.MULTI_SOURCE),
        credentials={"openalex": "user-key"},
        user_id="user-1",
    )

    assert sorted(item.title for item in result.papers) == [
        "OpenAlex record",
        "arXiv record one",
        "arXiv record two",
    ]


@pytest.mark.parametrize("message", ["Connection timed out", "429 Too Many Requests"])
@pytest.mark.asyncio
async def test_multisource_retains_success_and_reports_provider_failure(message):
    arxiv_result = paper(PaperSource.ARXIV, arxiv_id="2401.00001")
    openalex = FakeSearcher(ProviderError(message))
    search = service(
        arxiv=FakePaperRepository([arxiv_result]),
        openalex=openalex,
        cache=MemoryCache(),
    )

    result = await search.search(
        PaperSearch("question", source_scope=SourceScope.MULTI_SOURCE),
        credentials={"openalex": "user-key"},
        user_id="user-1",
    )

    assert result.papers == [arxiv_result]
    assert [execution.status for execution in result.execution] == [
        SourceExecutionStatus.COMPLETED,
        SourceExecutionStatus.FAILED,
    ]
    assert result.execution[1].error == message


class RecordingEnricher:
    def __init__(self, source):
        self.source = source
        self.calls = []

    async def enrich(self, identifiers, *, include_details=False):
        self.calls.append(identifiers)
        return [
            PaperEnrichment([identifier], {self.source: {"available": True}})
            for identifier in identifiers
        ]


@pytest.mark.asyncio
async def test_medicine_can_register_pubmed_wos_and_special_enrichment_by_configuration():
    shared = "10.1000/medicine"
    pubmed = FakeSearcher(
        [
            paper(
                "pubmed",
                doi=shared,
                title="PubMed basis",
                metadata={"pubmed": {"pmid": "123"}},
            )
        ]
    )
    wos = FakeSearcher(
        [
            paper(
                "web_of_science",
                doi=shared,
                title="WoS basis",
                metadata={"web_of_science": {"uid": "WOS:123"}},
            )
        ]
    )
    oa = FakeSearcher(
        [paper("openalex", doi=shared, metadata={"openalex": {"id": "W123"}})]
    )
    clinical = RecordingEnricher("clinical_trials")
    enrichment = {
        "clinical_trials": EnrichmentBinding(clinical, None, [IdentifierKind.DOI]),
    }
    bindings = {
        "pubmed": SearchBinding(pubmed),
        "web_of_science": SearchBinding(wos),
        "openalex": SearchBinding(oa, requires_api_key=True),
    }
    policy = FieldPolicy(
        default_search_plan=["pubmed"],
        multi_source_plan=["pubmed", "web_of_science", "openalex"],
        search_enrichers=["clinical_trials"],
    )
    search = PaperSearchService(
        bindings, {AcademicField.MEDICINE: policy}, MemoryCache(), enrichment
    )
    single = await search.search(PaperSearch("cancer", field=AcademicField.MEDICINE))
    assert single.papers[0].source == "pubmed"
    assert len(pubmed.calls) == 1 and not wos.calls and not oa.calls
    combined = await search.search(
        PaperSearch(
            "cancer",
            field=AcademicField.MEDICINE,
            source_scope=SourceScope.MULTI_SOURCE,
        ),
        credentials={"openalex": "user-key"},
        user_id="user",
    )
    assert [item.source for item in combined.execution] == [
        "pubmed",
        "web_of_science",
        "openalex",
    ]
    assert len(combined.papers) == 1
    assert combined.papers[0].title == "PubMed basis"
    assert set(combined.papers[0].metadata) == {
        "pubmed",
        "web_of_science",
        "openalex",
        "clinical_trials",
    }
    assert clinical.calls and wos.calls and oa.calls
    # 调整默认计划只改配置，仍使用同一缓存、融合和专有富化用例。
    changed = PaperSearchService(
        bindings,
        {
            AcademicField.MEDICINE: replace(
                policy, default_search_plan=["web_of_science"]
            )
        },
        MemoryCache(),
        enrichment,
    )
    result = await changed.search(PaperSearch("cancer", field=AcademicField.MEDICINE))
    assert result.papers[0].source == "web_of_science"


@pytest.mark.asyncio
async def test_field_configuration_controls_enrichment_after_final_limit():
    candidates = [paper("local", arxiv_id=f"2401.0000{i}") for i in (1, 2, 3)]
    searcher = FakeSearcher(candidates)
    specialized = RecordingEnricher("specialized")
    enrich = {
        "specialized": EnrichmentBinding(specialized, None, [IdentifierKind.ARXIV])
    }
    search = PaperSearchService(
        {"local": SearchBinding(searcher)},
        {
            AcademicField.GENERAL: FieldPolicy(["local"]),
            AcademicField.COMPUTER_SCIENCE: FieldPolicy(
                ["local"], search_enrichers=["specialized"]
            ),
        },
        MemoryCache(),
        enrich,
    )
    general = await search.search(PaperSearch("same CS papers", limit=2))
    assert not specialized.calls
    cs = await search.search(
        PaperSearch("same CS papers", field=AcademicField.COMPUTER_SCIENCE, limit=2)
    )
    assert len(specialized.calls[0]) == 2
    assert [p.arxiv_id for p in general.papers] == [p.arxiv_id for p in cs.papers]
    assert all("specialized" not in p.metadata for p in general.papers)
    assert all(p.metadata["specialized"]["available"] for p in cs.papers)
