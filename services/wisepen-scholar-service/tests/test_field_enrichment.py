"""通过公开用例验证通用短路、领域增量和新供应商接入，避免锁定内部实现。"""

import httpx
import pytest

from scholar.application.enrichment import EnrichmentBinding, EnrichmentRunner
from scholar.application.field_policies import FieldPolicy
from scholar.application.lookup import (
    EnrichmentStatus,
    LookupPurpose,
    LookupStatus,
    PaperLookup,
    PaperLookupService,
)
from scholar.application.search import (
    PaperSearch,
    PaperSearchService,
    SearchBinding,
    SourceScope,
)
from scholar.application.source_execution import SourceExecutionStatus
from scholar.core.storage.redis.paper_cache import RedisPaperEnrichmentCache
from scholar.domain.interfaces.paper_enricher import PaperEnrichment
from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import IdentifierKind, parse_identifier
from scholar.domain.repositories.paper_repository import PaperLookupMatch
from scholar.providers.enrichment_rules import arxiv_signals, s2_aliases, s2_signals
from scholar.providers.openalex import OpenAlexProvider
from test_enrichment import (
    FakeEnricher,
    FakePaperRepository,
    FakeRedisBoundary,
    make_paper,
    s2_enrichment,
)
from test_multisource_search import FakeSearcher, MemoryCache


def bindings(*, s2=None, hf=None, clinical=None, openalex=None, cache=None):
    sources = {
        "arxiv": EnrichmentBinding(
            None,
            None,
            [IdentifierKind.ARXIV, IdentifierKind.DOI],
            has_signals=arxiv_signals,
        ),
        "s2": EnrichmentBinding(
            s2,
            cache,
            [
                IdentifierKind.ARXIV,
                IdentifierKind.DOI,
                IdentifierKind.S2,
                IdentifierKind.CORPUS_ID,
            ],
            has_signals=s2_signals,
            aliases=s2_aliases,
        ),
    }
    for name, provider, kinds in [
        ("huggingface", hf, [IdentifierKind.ARXIV]),
        ("clinical_trials", clinical, [IdentifierKind.DOI]),
        (
            "openalex",
            openalex,
            [IdentifierKind.OPENALEX, IdentifierKind.DOI, IdentifierKind.ARXIV],
        ),
    ]:
        if provider is not None:
            sources[name] = EnrichmentBinding(provider, None, kinds)
    return sources


@pytest.mark.asyncio
async def test_local_general_signals_stop_chain_but_field_hf_still_runs():
    identity = parse_identifier("2401.00001")
    local = make_paper(metadata={"s2": {"citation_count": 0}})
    s2 = FakeEnricher(error=AssertionError("must not request general enrichment"))
    hf = FakeEnricher(
        {identity: PaperEnrichment([identity], {"huggingface": {"upvotes": 0}})}
    )
    service = PaperLookupService(
        FakePaperRepository({identity: PaperLookupMatch([local])}),
        enrichment=EnrichmentRunner(bindings(s2=s2, hf=hf)),
    )
    result = await service.lookup(
        PaperLookup(
            [identity.value], LookupPurpose.ENRICH, AcademicField.COMPUTER_SCIENCE
        )
    )
    assert result.items[0].metadata == {
        "s2": {"citation_count": 0},
        "huggingface": {"upvotes": 0},
    }
    assert result.items[0].enrichment_status == EnrichmentStatus.COMPLETE
    assert not s2.calls and len(hf.calls) == 1
    await service.lookup(PaperLookup([identity.value], LookupPurpose.ENRICH))
    assert len(hf.calls) == 1


@pytest.mark.asyncio
async def test_batch_only_unresolved_papers_advance_to_s2():
    first, second = map(parse_identifier, ["2401.00001", "2401.00002"])
    repository = FakePaperRepository(
        {first: PaperLookupMatch([make_paper(metadata={"s2": {"citation_count": 0}})])}
    )
    s2 = FakeEnricher({second: s2_enrichment(arxiv_id=second.value)})
    result = await PaperLookupService(
        repository, enrichment=EnrichmentRunner(bindings(s2=s2))
    ).lookup(
        PaperLookup([first.value, second.value, second.value], LookupPurpose.ENRICH)
    )
    assert s2.calls == [([second], False)]
    assert [item.status for item in result.items] == [LookupStatus.FOUND] * 3
    assert result.items[1].metadata == result.items[2].metadata


@pytest.mark.asyncio
async def test_s2_identity_alias_enables_hf_and_medicine_clinical_without_core_changes():
    corpus, arxiv, doi = map(
        parse_identifier, ["corpusid:123", "2401.00001", "10.5555/clinical"]
    )
    s2 = FakeEnricher({corpus: s2_enrichment(doi=doi.value)})
    hf = FakeEnricher(
        {arxiv: PaperEnrichment([arxiv], {"huggingface": {"github_stars": 0}})}
    )
    clinical = FakeEnricher(
        {
            doi: PaperEnrichment(
                [doi], {"clinical_trials": {"trial_ids": ["NCT00000001"]}}
            )
        }
    )
    runner = EnrichmentRunner(
        bindings(s2=s2, hf=hf, clinical=clinical), negative_ttl_seconds=60
    )
    policies = {
        AcademicField.MEDICINE: FieldPolicy(
            [], enrichers=["huggingface", "clinical_trials"]
        )
    }
    result = await PaperLookupService(
        FakePaperRepository(), enrichment=runner, policies=policies
    ).lookup(
        PaperLookup(["corpusid:123"], LookupPurpose.ENRICH, AcademicField.MEDICINE)
    )
    assert result.items[0].metadata["clinical_trials"]["trial_ids"] == ["NCT00000001"]
    assert hf.calls == [([arxiv], False)] and clinical.calls == [([doi], False)]


@pytest.mark.asyncio
async def test_general_fallback_success_is_complete_but_domain_failure_is_partial():
    identity = parse_identifier("2401.00001")

    class OpenAlexEnricher:
        async def enrich(self, identifiers, *, include_details=False, api_key=None):
            assert api_key == "request-key"
            return [
                PaperEnrichment([identifier], {"openalex": {"cited_by_count": 0}})
                for identifier in identifiers
            ]

    hf = FakeEnricher(error=ProviderError("HF unavailable"))
    service = PaperLookupService(
        FakePaperRepository(),
        enrichment=EnrichmentRunner(
            bindings(
                s2=FakeEnricher(error=ProviderError("S2 unavailable")),
                hf=hf,
                openalex=OpenAlexEnricher(),
            )
        ),
    )
    result = await service.lookup(
        PaperLookup([identity.value], LookupPurpose.ENRICH), api_key="request-key"
    )
    assert result.items[0].enrichment_status == EnrichmentStatus.COMPLETE
    assert result.items[0].error is None
    assert any(
        e.source == "s2" and e.status == SourceExecutionStatus.FAILED
        for e in result.execution
    )
    result = await service.lookup(
        PaperLookup(
            [identity.value], LookupPurpose.ENRICH, AcademicField.COMPUTER_SCIENCE
        ),
        api_key="request-key",
    )
    assert result.items[0].enrichment_status == EnrichmentStatus.PARTIAL
    assert result.items[0].metadata["openalex"]["cited_by_count"] == 0
    assert "HF unavailable" in result.items[0].error


@pytest.mark.asyncio
async def test_search_deduplicates_only_successful_searchers():
    identity = parse_identifier("2401.00001")
    paper = make_paper()
    hf = FakeEnricher(
        {identity: PaperEnrichment([identity], {"huggingface": {"upvotes": 3}})}
    )
    s2 = FakeEnricher(
        error=AssertionError(
            "successful common searcher must suppress common enrichment"
        )
    )
    searchers = {
        "arxiv": SearchBinding(FakeSearcher([paper])),
        "huggingface": SearchBinding(FakeSearcher(ProviderError("search failed"))),
    }
    service = PaperSearchService(
        searchers,
        {AcademicField.GENERAL: FieldPolicy(["arxiv", "huggingface"], ["huggingface"])},
        MemoryCache(),
        EnrichmentRunner(bindings(s2=s2, hf=hf)),
    )
    result = await service.search(
        PaperSearch("papers", source_scope=SourceScope.MULTI_SOURCE)
    )
    assert result.papers[0].metadata["huggingface"]["upvotes"] == 3
    assert len(hf.calls) == 1 and not s2.calls
    searchers["huggingface"].searcher = FakeSearcher([paper])
    await service.search(
        PaperSearch("successful domain search", source_scope=SourceScope.MULTI_SOURCE)
    )
    assert len(hf.calls) == 1


@pytest.mark.asyncio
async def test_openalex_enrichment_requires_key_preserves_fields_and_projects_full_only_on_request():
    requests = []
    work = {
        "id": "https://openalex.org/W77",
        "doi": "https://doi.org/10.5555/clinical",
        "title": "Trial",
        "abstract_inverted_index": None,
        "publication_date": None,
        "publication_year": 2024,
        "authorships": [
            {
                "author": {"id": "A1", "display_name": "Author", "orcid": None},
                "institutions": [{"id": "I1", "display_name": "Hospital"}],
            }
        ],
        "primary_location": None,
        "best_oa_location": None,
        "locations": [],
        "updated_date": "2026-10-27",
        "cited_by_count": 0,
        "is_retracted": False,
        "fwci": None,
        "mesh": [{"descriptor_ui": "D1"}],
        "study_designs": [{"id": "randomized-controlled-trial"}],
    }

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=work)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        provider = OpenAlexProvider(http)
        identity = parse_identifier("openalex:W77")
        with pytest.raises(ProviderError, match="key"):
            await provider.enrich([identity])
        for include_details in [False, True]:
            result = (
                await provider.enrich(
                    [identity], api_key="user-key", include_details=include_details
                )
            )[0]
            assert (result.paper is not None) == include_details
            assert result.metadata["openalex"]["cited_by_count"] == 0
            assert result.metadata["openalex"]["is_retracted"] is False
            assert (
                result.metadata["openalex"]["authorships"][0]["institutions"][0][
                    "display_name"
                ]
                == "Hospital"
            )
            assert result.metadata["openalex"]["study_designs"] == work["study_designs"]
    assert len(requests) == 2
    assert all(
        r.headers["authorization"] == "Bearer user-key"
        and "api_key" not in r.url.params
        for r in requests
    )


@pytest.mark.asyncio
async def test_local_failure_allows_general_fallback_and_preserves_original_error_on_total_failure():
    failure = ProviderError("local unavailable")

    class UnavailableRepository:
        async def lookup(self, identifiers):
            raise failure

    identity = parse_identifier("2401.00001")
    s2 = FakeEnricher({identity: s2_enrichment()})
    service = PaperLookupService(
        UnavailableRepository(), enrichment=EnrichmentRunner(bindings(s2=s2))
    )
    result = await service.lookup(PaperLookup([identity.value], LookupPurpose.ENRICH))
    assert result.items[0].enrichment_status == EnrichmentStatus.COMPLETE
    assert result.execution[0].status == SourceExecutionStatus.FAILED
    s2.error = ProviderError("S2 failed")
    with pytest.raises(ProviderError) as raised:
        await service.lookup(PaperLookup([identity.value], LookupPurpose.ENRICH))
    assert raised.value is failure


@pytest.mark.asyncio
async def test_byok_enrichment_cache_and_inflight_do_not_cross_credentials():
    identity = parse_identifier("openalex:W77")
    cache = RedisPaperEnrichmentCache(FakeRedisBoundary(), "openalex")

    class KeyedEnricher:
        def __init__(self):
            self.calls = []

        async def enrich(self, identifiers, *, include_details=False, api_key=None):
            self.calls.append(api_key)
            return [
                PaperEnrichment(
                    [identity], {"openalex": {"cited_by_count": len(self.calls)}}
                )
            ]

    provider = KeyedEnricher()
    service = PaperLookupService(
        FakePaperRepository(),
        enrichment=EnrichmentRunner(
            {
                "openalex": EnrichmentBinding(
                    provider, cache, [IdentifierKind.OPENALEX], requires_api_key=True
                ),
            }
        ),
    )
    query = PaperLookup(["openalex:W77"], LookupPurpose.ENRICH)
    await service.lookup(query, api_key="key-one")
    await service.lookup(query, api_key="key-two")
    result = await service.lookup(query, api_key="key-one")
    assert result.items[0].metadata["openalex"]["cited_by_count"] == 1
    assert provider.calls == ["key-one", "key-two"]
    with pytest.raises(ProviderError, match="key"):
        await service.lookup(query)
    assert all(
        "key-one" not in key and "key-two" not in key for key in cache.redis.values
    )
