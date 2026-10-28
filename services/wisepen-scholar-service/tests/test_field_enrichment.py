"""验证公共来源回退、身份别名接力和学科增量查询。"""

from datetime import date

import pytest
from elastic_transport import ConnectionError as ElasticConnectionError
from scholar.application.field_policies import FieldPolicy
from scholar.application.lookup import (
    EnrichmentStatus,
    LookupPurpose,
    LookupStatus,
    PaperLookup,
    PaperLookupService,
)
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.source_execution import SourceExecutionStatus
from scholar.core.storage.elasticsearch.paper_repository import (
    ElasticsearchPaperRepository,
)
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import IdentifierKind, parse_identifier
from scholar.domain.models.paper import Paper, PaperSource
from scholar.providers.arxiv import ArxivProvider, has_signals as arxiv_signals
from scholar.providers.openalex import has_signals as openalex_signals
from scholar.providers.s2 import aliases as s2_aliases, has_signals as s2_signals


class FakeProvider:
    def __init__(self, matches=None, *, error=None):
        self.matches = matches or {}
        self.error = error
        self.calls = []

    async def lookup(self, ids, *, api_key=None):
        ids = list(ids)
        self.calls.append((ids, api_key))
        if self.error is not None:
            raise self.error
        return [self.matches.get(identity, PaperLookupMatch([])) for identity in ids]


def make_paper(
    source=PaperSource.S2,
    *,
    arxiv_id="2401.00001",
    doi=None,
    title="Lookup paper",
    metadata=None,
):
    return Paper(
        arxiv_id=arxiv_id,
        doi=doi,
        title=title,
        abstract="Research abstract",
        authors=["Researcher"],
        publication_date=date(2024, 1, 2),
        year=2024,
        url="https://example.test/paper",
        pdf_url=None,
        source=source,
        metadata={} if metadata is None else metadata,
    )


@pytest.mark.asyncio
async def test_s2_aliases_route_domain_providers_without_changing_core_identity():
    corpus = parse_identifier("corpusid:123")
    arxiv = parse_identifier("2401.00001")
    doi = parse_identifier("10.5555/clinical")
    s2_paper = make_paper(
        arxiv_id=arxiv.value,
        doi=doi.value,
        metadata={
            "s2": {
                "paper_id": "a" * 40,
                "corpus_id": "123",
                "citation_count": 8,
                "tldr": {"text": "S2 summary"},
            }
        },
    )
    s2 = FakeProvider({corpus: PaperLookupMatch([s2_paper])})
    hf = FakeProvider(
        {
            arxiv: PaperLookupMatch(
                [
                    make_paper(
                        PaperSource.HUGGINGFACE,
                        arxiv_id=arxiv.value,
                        metadata={"huggingface": {"github_stars": 0}},
                    )
                ]
            )
        }
    )
    clinical = FakeProvider(
        {
            doi: PaperLookupMatch(
                [
                    make_paper(
                        "clinical_trials",
                        arxiv_id=None,
                        doi=doi.value,
                        metadata={"clinical_trials": {"trial_ids": ["NCT1"]}},
                    )
                ]
            )
        }
    )
    policies = {
        AcademicField.MEDICINE: FieldPolicy(
            [], [PaperSource.HUGGINGFACE, "clinical_trials"]
        )
    }
    runner = LookupRunner(
        {
            PaperSource.S2: LookupBinding(
                s2,
                None,
                [IdentifierKind.CORPUS_ID],
                has_signals=s2_signals,
                aliases=s2_aliases,
            ),
            PaperSource.HUGGINGFACE: LookupBinding(hf, None, [IdentifierKind.ARXIV]),
            "clinical_trials": LookupBinding(clinical, None, [IdentifierKind.DOI]),
        }
    )

    result = await PaperLookupService(runner, policies=policies).lookup(
        PaperLookup(
            [f"corpusid:{corpus.value}"], LookupPurpose.FULL, AcademicField.MEDICINE
        )
    )

    assert result.items[0].status == LookupStatus.FOUND
    assert result.items[0].paper.arxiv_id == arxiv.value
    assert result.items[0].paper.doi == doi.value
    assert result.items[0].paper.metadata["s2"]["citation_count"] == 8
    assert result.items[0].paper.metadata["huggingface"]["github_stars"] == 0
    assert result.items[0].paper.metadata["clinical_trials"]["trial_ids"] == ["NCT1"]
    assert s2.calls == [([corpus], None)]
    assert hf.calls == [([arxiv], None)]
    assert clinical.calls == [([doi], None)]


@pytest.mark.asyncio
async def test_only_unresolved_targets_advance_to_the_next_common_source():
    first, second = map(parse_identifier, ["2401.00001", "2401.00002"])
    local = make_paper(
        PaperSource.ARXIV,
        arxiv_id=first.value,
        metadata={"s2": {"citation_count": 0}},
    )
    arxiv = FakeProvider({first: PaperLookupMatch([local])})
    s2 = FakeProvider(
        {
            second: PaperLookupMatch(
                [
                    make_paper(
                        arxiv_id=second.value,
                        metadata={"s2": {"citation_count": 2}},
                    )
                ]
            )
        }
    )
    runner = LookupRunner(
        {
            PaperSource.ARXIV: LookupBinding(
                arxiv,
                None,
                [IdentifierKind.ARXIV],
                has_signals=arxiv_signals,
            ),
            PaperSource.S2: LookupBinding(
                s2, None, [IdentifierKind.ARXIV], has_signals=s2_signals
            ),
        }
    )

    result = await PaperLookupService(runner).lookup(
        PaperLookup([first.value, second.value, second.value], LookupPurpose.ENRICH)
    )

    assert [item.status for item in result.items] == [LookupStatus.FOUND] * 3
    assert result.items[1].metadata == result.items[2].metadata
    assert arxiv.calls == [([first, second], None)]
    assert s2.calls == [([second], None)]


@pytest.mark.asyncio
async def test_general_fallback_success_is_complete_but_domain_failure_is_partial():
    identity = parse_identifier("2401.00001")
    arxiv_error = ProviderError("local lookup unavailable")
    s2_error = ProviderError("S2 unavailable")
    arxiv = FakeProvider(error=arxiv_error)
    s2 = FakeProvider(error=s2_error)
    openalex_paper = make_paper(
        PaperSource.OPENALEX,
        metadata={"openalex": {"cited_by_count": 0}},
    )
    openalex = FakeProvider({identity: PaperLookupMatch([openalex_paper])})
    hf = FakeProvider(error=ProviderError("HF unavailable"))
    runner = LookupRunner(
        {
            PaperSource.ARXIV: LookupBinding(
                arxiv, None, [IdentifierKind.ARXIV], has_signals=arxiv_signals
            ),
            PaperSource.S2: LookupBinding(
                s2, None, [IdentifierKind.ARXIV], has_signals=s2_signals
            ),
            PaperSource.OPENALEX: LookupBinding(
                openalex,
                None,
                [IdentifierKind.ARXIV],
                has_signals=openalex_signals,
            ),
            PaperSource.HUGGINGFACE: LookupBinding(hf, None, [IdentifierKind.ARXIV]),
        }
    )
    policies = {
        AcademicField.COMPUTER_SCIENCE: FieldPolicy([], [PaperSource.HUGGINGFACE])
    }

    result = await PaperLookupService(runner, policies=policies).lookup(
        PaperLookup(
            [identity.value], LookupPurpose.FULL, AcademicField.COMPUTER_SCIENCE
        ),
        credentials={PaperSource.OPENALEX: "user-key"},
    )

    assert result.items[0].status == LookupStatus.FOUND
    assert result.items[0].enrichment_status == EnrichmentStatus.PARTIAL
    assert result.items[0].paper.metadata["openalex"]["cited_by_count"] == 0
    assert result.items[0].error == "HF unavailable"
    assert [(item.source, item.status) for item in result.execution] == [
        (PaperSource.ARXIV, SourceExecutionStatus.FAILED),
        (PaperSource.S2, SourceExecutionStatus.FAILED),
        (PaperSource.OPENALEX, SourceExecutionStatus.COMPLETED),
        (PaperSource.HUGGINGFACE, SourceExecutionStatus.FAILED),
    ]
    assert openalex.calls == [([identity], "user-key")]


@pytest.mark.asyncio
async def test_real_elasticsearch_lookup_error_is_visible_while_s2_fallback_succeeds():
    class UnavailableElasticsearch:
        async def msearch(self, **kwargs):
            raise ElasticConnectionError("ES connection lost")

    identity = parse_identifier("2401.00001")
    repository = ElasticsearchPaperRepository(UnavailableElasticsearch(), "test-alias")
    arxiv = ArxivProvider(repository, embedding_client=None)
    s2_paper = make_paper(
        metadata={
            "s2": {
                "citation_count": 4,
                "tldr": {"text": "Contribution"},
            }
        }
    )
    s2 = FakeProvider({identity: PaperLookupMatch([s2_paper])})
    runner = LookupRunner(
        {
            PaperSource.ARXIV: LookupBinding(
                arxiv,
                None,
                [IdentifierKind.ARXIV],
                has_signals=arxiv_signals,
            ),
            PaperSource.S2: LookupBinding(
                s2, None, [IdentifierKind.ARXIV], has_signals=s2_signals
            ),
        }
    )

    result = await PaperLookupService(runner).lookup(
        PaperLookup([identity.value], LookupPurpose.ENRICH)
    )

    assert result.items[0].status == LookupStatus.FOUND
    assert result.items[0].enrichment_status == EnrichmentStatus.COMPLETE
    assert result.items[0].metadata["s2"]["citation_count"] == 4
    assert result.execution[0].status == SourceExecutionStatus.FAILED
    assert "Connection error" in result.execution[0].error
