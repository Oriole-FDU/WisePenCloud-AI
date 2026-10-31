import asyncio

from policy_fakes import make_policy_store

from pdf_fakes import make_pdf_service
from scholar.core.providers import PROVIDER_POLICIES, ProviderPolicy
from dataclasses import replace
from datetime import date
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError
from common.core.exceptions import ServiceException
from scholar.domain.models.fields import FieldPolicy
from policy_fakes import TEST_FIELD_POLICIES
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.search import (
    PaperSearch,
    PaperSearchService,
    SearchBinding,
    SearchResult,
    SourceScope,
)
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import IdentifierKind
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.models.source_execution import (
    SourceExecutionStatus,
)
from scholar.core.providers.arxiv import ARXIV_CATEGORIES, ArxivProvider
from scholar.core.providers.openalex import OPENALEX_FILTERS


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
        return PaperSearchBatch(self.papers)


class FakeSearcher:
    def __init__(self, search=None):
        self.search_result = search or []
        self.calls = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        if getattr(self, "requires_key", False) and not kwargs.get("api_key"):
            raise ProviderError("OpenAlex API key is required")
        if isinstance(self.search_result, Exception):
            raise self.search_result
        if callable(self.search_result):
            return PaperSearchBatch(self.search_result(kwargs))
        return PaperSearchBatch(self.search_result)


@pytest.mark.asyncio
async def test_bulk_candidates_reused_across_limits_and_byok_overrides_platform():
    """持有 BYOK 才能使用共享搜索缓存；平台 key 不参与搜索。"""
    provider = FakeSearcher(
        [paper(PaperSource.OPENALEX, doi=f"10.1000/{index}") for index in range(12)]
    )
    cache = MemoryCache()
    search = PaperSearchService(
        {"openalex": SearchBinding(provider, candidate_limit=200, requires_byok=True)},
        make_policy_store({AcademicField.GENERAL: FieldPolicy(searchers=["openalex"])}),
        cache,
        LookupRunner({}),
        platform_credentials={"openalex": "platform-key"},
    pdf=make_pdf_service())
    with pytest.raises(ServiceException, match="BYOK"):
        await search.search(PaperSearch("same"))
    small = await search.search(PaperSearch("same", limit=2), credentials={"openalex": "user-a"})
    larger = await search.search(PaperSearch("same", limit=10), credentials={"openalex": "user-a"})
    assert len(small.papers) == 2
    assert len(larger.papers) == 10
    assert larger.execution[0].cache_hit
    assert [call["api_key"] for call in provider.calls] == ["user-a"]
    assert provider.calls[0]["limit"] == 200
    shared = await search.search(
        PaperSearch("same"), credentials={"openalex": "user-key"}
    )
    assert shared.execution[0].cache_hit
    assert len(provider.calls) == 1
    with pytest.raises(ServiceException, match="BYOK"):
        await search.search(PaperSearch("same"))
    await search.search(PaperSearch("new query"), credentials={"openalex": "user-key"})
    assert [call["api_key"] for call in provider.calls] == ["user-a", "user-key"]


@pytest.mark.asyncio
async def test_byok_quota_failure_uses_normal_search_fallback_without_platform_retry():
    quota = FakeSearcher(ProviderError("429 quota exhausted"))
    fallback = FakeSearcher([paper(PaperSource.ARXIV, arxiv_id="2501.12948")])
    search = PaperSearchService(
        {"openalex": SearchBinding(quota, requires_byok=True), "arxiv": SearchBinding(fallback)},
        make_policy_store({AcademicField.GENERAL: FieldPolicy(searchers=["openalex", "arxiv"])}),
        MemoryCache(),
        LookupRunner({}),
        platform_credentials={"openalex": "platform-key"},
    pdf=make_pdf_service())
    result = await search.search(PaperSearch("reasoning"), credentials={"openalex": "user-key"})
    assert [call["api_key"] for call in quota.calls] == ["user-key"]
    assert result.papers[0].arxiv_id == "2501.12948"
    assert [item.status for item in result.execution] == [
        SourceExecutionStatus.FAILED,
        SourceExecutionStatus.COMPLETED,
    ]


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


def service(*, arxiv=None, openalex=None, huggingface=None, cache=None):
    openalex = openalex or FakeSearcher()
    openalex.requires_key = True
    return PaperSearchService(
        searchers={
            "arxiv": SearchBinding(arxiv or FakeSearcher()),
            "huggingface": SearchBinding(huggingface or FakeSearcher(), requires_byok=True),
            "openalex": SearchBinding(openalex or FakeSearcher(), requires_byok=True),
        },
        policies=make_policy_store({
            field: replace(policy, field_enrichers=[])
            for field, policy in TEST_FIELD_POLICIES.items()
        }),
        lookup=LookupRunner({}),
        query_cache=cache or MemoryCache(),
    pdf=make_pdf_service())


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
    search = service(
        arxiv=arxiv,
        openalex=oa,
        huggingface=FakeSearcher(ProviderError("HF unavailable")),
        cache=cache,
    )

    openalex_result = await search.search(
        PaperSearch("law", field=AcademicField.LAW),
        credentials={"openalex": "law-key"},
    )
    fallback_result = await search.search(PaperSearch("general terms"))

    assert oa.calls[0]["field"] == AcademicField.LAW
    assert oa.calls[0]["api_key"] == "law-key"
    assert len(oa.calls) == 1
    assert arxiv.calls[0]["field"] == AcademicField.GENERAL
    assert [item.title for item in fallback_result.papers] == ["First", "Second"]
    assert openalex_result.execution[0].source == PaperSource.OPENALEX


@pytest.mark.asyncio
async def test_arxiv_provider_maps_shared_field_and_does_not_forward_api_key():
    repository = FakePaperRepository()
    repository.search = AsyncMock(return_value=[])
    provider = ArxivProvider(
        repository,
        AsyncMock(
            embed=AsyncMock(
                side_effect=APIConnectionError(
                    request=httpx.Request("POST", "http://encoder")
                )
            )
        ),
    )

    await provider.search(
        query="genomics",
        field=AcademicField.BIOLOGY,
        published_after=None,
        published_before=None,
        limit=5,
        api_key="ignored-by-arxiv",
    )

    assert repository.search.call_args.kwargs["categories"] == ["q-bio.*"]
    assert "api_key" not in repository.search.call_args.kwargs


@pytest.mark.asyncio
async def test_multisource_missing_key_keeps_successful_local_source():
    arxiv = FakePaperRepository()
    openalex = FakeSearcher()
    search = service(arxiv=arxiv, openalex=openalex)
    result = await search.search(
        PaperSearch("question", source_scope=SourceScope.MULTI_SOURCE)
    )
    assert result.papers == []
    assert {e.source: e.status for e in result.execution} == {
        "openalex": SourceExecutionStatus.NOT_EXECUTED,
        "arxiv": SourceExecutionStatus.COMPLETED,
    }
    assert len(arxiv.calls) == 1
    assert openalex.calls == []
    single = await search.search(
        PaperSearch(
            "law", field=AcademicField.LAW, source_scope=SourceScope.MULTI_SOURCE
        ),
        credentials={"openalex": "law-key"},
    )
    assert [e.source for e in single.execution] == ["openalex"]


@pytest.mark.asyncio
async def test_openalex_cache_shared_only_when_user_supplies_byok():
    cache = MemoryCache()
    openalex = FakeSearcher(
        [paper(PaperSource.OPENALEX, doi="10.1000/shared", title="Shared paper")]
    )
    search = service(openalex=openalex, cache=cache)
    query = PaperSearch("same query")

    first_a = await search.search(
        query, credentials={"openalex": "key-a"}
    )
    changed_key = await search.search(
        query, credentials={"openalex": "key-b"}
    )
    without_key = await search.search(query)
    cached_a = await search.search(
        query, credentials={"openalex": "key-a"}
    )

    assert first_a.papers[0].title == "Shared paper"
    assert not first_a.execution[0].cache_hit
    for result in (changed_key, cached_a):
        assert result.papers == first_a.papers
        assert result.execution[0].cache_hit
    assert len(openalex.calls) == 1
    assert openalex.calls[0]["api_key"] == "key-a"
    assert without_key.papers == []
    assert without_key.execution[0].status == SourceExecutionStatus.NOT_EXECUTED
    assert len(cache.put_calls) == 2


@pytest.mark.asyncio
async def test_multisource_fuses_by_exact_ids_and_keeps_identifier_conflicts_visible():
    arxiv_papers = [
        paper(
            PaperSource.ARXIV,
            arxiv_id="1706.03762",
            doi="10.48550/arxiv.1706.03762",
            title="arXiv facts",
            metadata={"arxiv": {"categories": ["cs.CL"]}},
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
    )

    assert isinstance(result, SearchResult)
    assert result.papers[0].title == "OpenAlex facts"
    assert result.papers[0].doi == "10.1000/openalex-doi"
    assert result.papers[0].metadata == {
        "openalex": {"id": "W1"},
        "arxiv": {"categories": ["cs.CL"]},
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
    )

    assert result.papers == [replace(arxiv_result, pdf_url="https://arxiv.org/pdf/2401.00001")]
    assert {e.source: e.status for e in result.execution} == {
        "arxiv": SourceExecutionStatus.COMPLETED,
        "openalex": SourceExecutionStatus.FAILED,
    }
    assert result.execution[0].error == message


@pytest.mark.asyncio
async def test_multisource_deadline_keeps_the_completed_source():
    class SlowSearcher:
        async def search(self, **kwargs):
            await asyncio.Event().wait()

    found = paper(PaperSource.ARXIV, arxiv_id="2401.00001")
    search = service(arxiv=FakePaperRepository([found]), openalex=SlowSearcher())
    result = await search.search(
        PaperSearch("partial deadline", source_scope=SourceScope.MULTI_SOURCE),
        credentials={"openalex": "user-key"}, timeout=0.03,
    )

    assert result.papers[0].arxiv_id == found.arxiv_id
    failures = [item for item in result.execution if item.status == SourceExecutionStatus.FAILED]
    assert len(failures) == 1
    assert failures[0].source == "openalex"
    assert "deadline" in failures[0].error


@pytest.mark.asyncio
async def test_caller_cancellation_is_not_a_search_deadline_failure():
    started = asyncio.Event()

    class SlowSearcher:
        async def search(self, **kwargs):
            started.set()
            await asyncio.Event().wait()

    search = service(openalex=SlowSearcher())
    task = asyncio.create_task(search.search(
        PaperSearch("cancel"), credentials={"openalex": "user-key"}, timeout=1,
    ))
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class RecordingEnricher:
    def __init__(self, source):
        self.source = source
        self.calls = []

    async def lookup(self, identifiers, *, api_key=None):
        identifiers = list(identifiers)
        self.calls.append((identifiers, api_key))
        return [
            PaperLookupMatch(
                [
                    paper(
                        self.source,
                        arxiv_id=identifier.value
                        if identifier.kind == IdentifierKind.ARXIV
                        else None,
                        doi=identifier.value
                        if identifier.kind == IdentifierKind.DOI
                        else None,
                        metadata={self.source: {"available": True}},
                    )
                ]
            )
            for identifier in identifiers
        ]


@pytest.mark.asyncio
async def test_medicine_can_register_pubmed_wos_and_special_enrichment_by_configuration(monkeypatch):
    for source in ("pubmed", "web_of_science", "clinical_trials"):
        monkeypatch.setitem(PROVIDER_POLICIES, source, ProviderPolicy([IdentifierKind.DOI], lambda paper, source=source: paper.metadata[source]))
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
        "clinical_trials": LookupBinding(clinical, None, PROVIDER_POLICIES["clinical_trials"]),
    }
    bindings = {
        "pubmed": SearchBinding(pubmed),
        "web_of_science": SearchBinding(wos),
        "openalex": SearchBinding(oa),
    }
    policy = FieldPolicy(
        searchers=["pubmed", "web_of_science", "openalex"],
        field_enrichers=["clinical_trials"],
    )
    search = PaperSearchService(
        bindings,
        make_policy_store({AcademicField.MEDICINE: policy}),
        MemoryCache(),
        LookupRunner(enrichment),
    pdf=make_pdf_service())
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
        make_policy_store({AcademicField.MEDICINE: replace(policy, searchers=["web_of_science"])}),
        MemoryCache(),
        LookupRunner(enrichment),
    pdf=make_pdf_service())
    result = await changed.search(PaperSearch("cancer", field=AcademicField.MEDICINE))
    assert result.papers[0].source == "web_of_science"


@pytest.mark.asyncio
async def test_field_configuration_controls_enrichment_after_final_limit(monkeypatch):
    monkeypatch.setitem(PROVIDER_POLICIES, "specialized", ProviderPolicy([IdentifierKind.ARXIV], lambda paper: paper.metadata["specialized"]))
    candidates = [paper("local", arxiv_id=f"2401.0000{i}") for i in (1, 2, 3)]
    searcher = FakeSearcher(candidates)
    specialized = RecordingEnricher("specialized")
    enrich = {"specialized": LookupBinding(specialized, None, PROVIDER_POLICIES["specialized"])}
    search = PaperSearchService(
        {"local": SearchBinding(searcher)},
        make_policy_store({
            AcademicField.GENERAL: FieldPolicy(["local"]),
            AcademicField.COMPUTER_SCIENCE: FieldPolicy(
                ["local"], field_enrichers=["specialized"]
            ),
        }),
        MemoryCache(),
        LookupRunner(enrich),
    pdf=make_pdf_service())
    general = await search.search(PaperSearch("same CS papers", limit=2))
    assert not specialized.calls
    cs = await search.search(
        PaperSearch("same CS papers", field=AcademicField.COMPUTER_SCIENCE, limit=2)
    )
    assert len(specialized.calls[0][0]) == 2
    assert [p.arxiv_id for p in general.papers] == [p.arxiv_id for p in cs.papers]
    assert all("specialized" not in p.metadata for p in general.papers)
    assert all(p.metadata["specialized"]["available"] for p in cs.papers)


@pytest.mark.asyncio
async def test_default_falls_back_only_on_provider_failure_and_stops_on_empty_result():
    secondary = FakeSearcher([paper("secondary", doi="10.1000/secondary")])
    primary = FakeSearcher(ProviderError("primary offline"))
    search = PaperSearchService(
        {"primary": SearchBinding(primary), "secondary": SearchBinding(secondary)},
        make_policy_store({AcademicField.GENERAL: FieldPolicy(["primary", "secondary"])}),
        MemoryCache(),
        LookupRunner({}),
    pdf=make_pdf_service())
    result = await search.search(PaperSearch("fallback"))
    assert result.papers[0].source == "secondary"
    assert [e.status for e in result.execution] == [
        SourceExecutionStatus.FAILED,
        SourceExecutionStatus.COMPLETED,
    ]
    primary.search_result = []
    result = await search.search(PaperSearch("empty is success"))
    assert result.papers == []
    assert len(result.execution) == 1
    assert len(secondary.calls) == 1
    primary.search_result = TypeError("program bug")
    with pytest.raises(TypeError, match="program bug"):
        await search.search(PaperSearch("bug"))
