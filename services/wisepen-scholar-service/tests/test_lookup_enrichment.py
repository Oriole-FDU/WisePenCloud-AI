"""lookup 的 purpose、身份路由和 OpenAlex/S2 组合合同。"""

from dataclasses import replace
from datetime import date

import pytest
from scholar.application.enrichment import EnrichmentBinding, EnrichmentRunner
from scholar.application.lookup import (
    EnrichmentStatus,
    LookupPurpose,
    LookupStatus,
    PaperLookup,
    PaperLookupService,
)
from scholar.domain.interfaces.paper_enricher import PaperEnrichment
from scholar.domain.models.identifiers import IdentifierKind, parse_identifier
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.repositories.paper_repository import PaperLookupMatch
from scholar.providers.enrichment_rules import arxiv_signals, s2_aliases, s2_signals


def make_paper(
    source=PaperSource.ARXIV,
    *,
    arxiv_id="2401.00001",
    doi=None,
    title="Offline title",
    metadata=None,
):
    return Paper(
        arxiv_id=arxiv_id,
        doi=doi,
        title=title,
        abstract="Offline abstract",
        authors=["Offline author"],
        publication_date=date(2024, 1, 2),
        year=2024,
        url="https://example.test/paper",
        pdf_url=None,
        source=source,
        metadata={} if metadata is None else metadata,
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
    def __init__(self, values=None):
        self.values = values or {}
        self.puts = []

    async def get(self, identifiers):
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
    def __init__(self, responses=None, *, error=None):
        self.responses = responses or {}
        self.error = error
        self.calls = []

    async def enrich(self, identifiers, *, include_details=False, api_key=None):
        identifiers = list(identifiers)
        self.calls.append((identifiers, include_details))
        if self.error is not None:
            raise self.error
        responses = [self.responses.get(identifier) for identifier in identifiers]
        return (
            responses
            if include_details
            else [
                None if response is None else replace(response, paper=None)
                for response in responses
            ]
        )


def s2_result(
    *,
    arxiv_id="2403.00003",
    doi="10.5555/resolved",
    s2_id="b" * 40,
    corpus_id=77,
    full_paper=True,
):
    identifiers = [parse_identifier("s2:" + s2_id)]
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
            "citation_count": 4,
            "influential_citation_count": 1,
            "is_open_access": False,
            "tldr": {"model": "tldr@v2", "text": "Contribution"},
        }
    }
    paper = (
        make_paper(
            PaperSource.S2,
            arxiv_id=arxiv_id,
            doi=doi,
            title="S2 details",
            metadata=metadata,
        )
        if full_paper
        else None
    )
    return PaperEnrichment(identifiers, metadata, paper)


def lookup_service(
    repository, openalex_lookup=None, *, s2_enricher=None, s2_cache=None
):
    return PaperLookupService(
        repository,
        openalex_lookup,
        enrichment=EnrichmentRunner(
            {
                "arxiv": EnrichmentBinding(
                    None,
                    None,
                    [IdentifierKind.ARXIV, IdentifierKind.DOI],
                    has_signals=arxiv_signals,
                ),
                "s2": EnrichmentBinding(
                    s2_enricher,
                    s2_cache,
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
        ),
    )


@pytest.mark.asyncio
async def test_details_never_calls_s2_and_cache_only_miss_is_not_found():
    arxiv = parse_identifier("2401.00001")
    local = make_paper()
    repository = FakePaperRepository({arxiv: PaperLookupMatch([local])})
    enricher = FakeEnricher()
    cache = FakeCache()

    result = await lookup_service(
        repository, s2_enricher=enricher, s2_cache=cache
    ).lookup(
        PaperLookup(
            ["arxiv:2401.00001", "s2:" + "a" * 40],
            purpose=LookupPurpose.DETAILS,
        )
    )

    assert [item.status for item in result.items] == [
        LookupStatus.FOUND,
        LookupStatus.NOT_FOUND,
    ]
    assert result.items[0].paper == local
    assert result.items[1].paper is None
    assert enricher.calls == []


@pytest.mark.asyncio
async def test_enrich_uses_local_paper_without_openalex_or_full_paper_projection():
    arxiv = parse_identifier("2402.00002")
    local_paper = make_paper(arxiv_id=arxiv.value)
    repository = FakePaperRepository({arxiv: PaperLookupMatch([local_paper])})
    enricher = FakeEnricher({arxiv: s2_result(arxiv_id=arxiv.value, full_paper=True)})
    openalex_calls = []

    async def openalex_lookup(identifier, *, api_key):
        openalex_calls.append((identifier, api_key))

    result = await lookup_service(
        repository,
        openalex_lookup=openalex_lookup,
        s2_enricher=enricher,
        s2_cache=FakeCache(),
    ).lookup(
        PaperLookup([arxiv.value], purpose=LookupPurpose.ENRICH),
        api_key="request-key",
    )

    assert result.items[0].status == LookupStatus.FOUND
    assert result.items[0].paper is None
    assert result.items[0].metadata["s2"]["citation_count"] == 4
    assert result.items[0].enrichment_status == EnrichmentStatus.COMPLETE
    assert openalex_calls == []
    assert enricher.calls == [([arxiv], False)]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,value", [("s2", "c" * 40), ("corpusid", "215416146")])
async def test_enrich_and_full_accept_semanticscholar_identity_kinds(kind, value):
    identifier = parse_identifier(f"{kind}:{value}")
    response = s2_result(
        arxiv_id=None,
        doi=None,
        s2_id=value if kind == "s2" else "b" * 40,
        corpus_id=value if kind == "corpusid" else None,
    )
    enricher = FakeEnricher({identifier: response})
    service = lookup_service(
        FakePaperRepository(), s2_enricher=enricher, s2_cache=FakeCache()
    )

    enriched = await service.lookup(
        PaperLookup([f"{kind}:{value}"], purpose=LookupPurpose.ENRICH)
    )
    complete = await service.lookup(
        PaperLookup([f"{kind}:{value}"], purpose=LookupPurpose.FULL)
    )

    assert enriched.items[0].status == LookupStatus.FOUND
    assert enriched.items[0].paper is None
    assert complete.items[0].status == LookupStatus.FOUND
    assert complete.items[0].paper.source == PaperSource.S2
    assert [details for _, details in enricher.calls] == [False, True]
