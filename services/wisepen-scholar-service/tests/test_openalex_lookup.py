import asyncio
from datetime import date

import httpx
import pytest
from common.core.exceptions import ServiceException

from scholar.application.lookup import (
    LookupPurpose,
    LookupStatus,
    PaperLookup,
    PaperLookupService,
)
from scholar.application.source_execution import SourceExecutionStatus
from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.identifiers import IdentifierKind, PaperIdentifier
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.repositories.paper_repository import PaperLookupMatch


def make_paper(source, *, arxiv_id=None, doi=None, title="A paper"):
    return Paper(
        arxiv_id=arxiv_id,
        doi=doi,
        title=title,
        abstract="An abstract",
        authors=["A. Researcher"],
        publication_date=date(2024, 1, 2),
        year=2024,
        url=f"https://{source.value}.example/paper",
        pdf_url=None,
        source=source,
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


@pytest.mark.asyncio
async def test_lookup_keeps_local_hits_ambiguity_and_failure_without_openalex_fallback():
    arxiv = PaperIdentifier(IdentifierKind.ARXIV, "2401.00001")
    doi = PaperIdentifier(IdentifierKind.DOI, "10.1234/ambiguous")
    failed = PaperIdentifier(IdentifierKind.ARXIV, "2401.00002")
    local_paper = make_paper(PaperSource.ARXIV, arxiv_id=arxiv.value)
    repository = FakePaperRepository(
        {
            arxiv: PaperLookupMatch([local_paper]),
            doi: PaperLookupMatch(
                [
                    make_paper(PaperSource.ARXIV, doi=doi.value, title="First"),
                    make_paper(PaperSource.ARXIV, doi=doi.value, title="Second"),
                ]
            ),
            failed: PaperLookupMatch([], error="msearch item rejected"),
        }
    )
    openalex_calls = []

    async def openalex_lookup(identifier, *, api_key):
        openalex_calls.append((identifier, api_key))

    service = PaperLookupService(repository, openalex_lookup)
    result = await service.lookup(
        PaperLookup(
            [
                "https://arxiv.org/abs/2401.00001v2",
                "arxiv:2401.00001v1",
                "doi:10.1234/ambiguous",
                "arxiv:2401.00002",
                "s2:" + "a" * 40,
            ]
        ),
        api_key="request-key",
    )

    assert [item.status for item in result.items] == [
        LookupStatus.FOUND,
        LookupStatus.FOUND,
        LookupStatus.AMBIGUOUS,
        LookupStatus.FAILED,
        LookupStatus.UNSUPPORTED_IDENTIFIER,
    ]
    assert [item.identifier for item in result.items[:2]] == [
        "https://arxiv.org/abs/2401.00001v2",
        "arxiv:2401.00001v1",
    ]
    assert result.items[0].paper == local_paper
    assert result.items[1].paper == local_paper
    assert result.items[3].error == "msearch item rejected"
    assert repository.calls == [[arxiv, doi, failed]]
    assert openalex_calls == []
    assert [item.status for item in result.execution] == [SourceExecutionStatus.PARTIAL]
    assert result.execution[0].error == "msearch item rejected"


@pytest.mark.asyncio
async def test_lookup_fills_only_local_not_found_once_per_normalized_identifier():
    doi = PaperIdentifier(IdentifierKind.DOI, "10.1234/example")
    arxiv = PaperIdentifier(IdentifierKind.ARXIV, "2402.00001")
    openalex_id = PaperIdentifier(IdentifierKind.OPENALEX, "W77")
    repository = FakePaperRepository()
    openalex_paper = make_paper(PaperSource.OPENALEX, doi=doi.value, title="Resolved")
    calls = []

    async def openalex_lookup(identifier, *, api_key):
        calls.append((identifier, api_key))
        return {doi: openalex_paper, openalex_id: openalex_paper}.get(identifier)

    service = PaperLookupService(repository, openalex_lookup)
    result = await service.lookup(
        PaperLookup(
            [
                "doi:10.1234/EXAMPLE",
                "https://doi.org/10.1234/example",
                "arxiv:2402.00001",
                "openalex:W77",
            ]
        ),
        api_key="current-request-key",
    )

    assert [item.status for item in result.items] == [
        LookupStatus.FOUND,
        LookupStatus.FOUND,
        LookupStatus.NOT_FOUND,
        LookupStatus.FOUND,
    ]
    assert [item.paper for item in result.items] == [
        openalex_paper,
        openalex_paper,
        None,
        openalex_paper,
    ]
    assert [identifier for identifier, _ in calls] == [doi, arxiv, openalex_id]
    assert {key for _, key in calls} == {"current-request-key"}
    assert repository.calls == [[doi, arxiv]]
    assert [item.source for item in result.execution] == [
        PaperSource.ARXIV,
        PaperSource.OPENALEX,
    ]
    assert result.execution[1].status == SourceExecutionStatus.COMPLETED
    assert "current-request-key" not in repr(result)


@pytest.mark.asyncio
async def test_lookup_reports_missing_key_only_for_openalex_identity():
    repository = FakePaperRepository()
    openalex_calls = []

    async def openalex_lookup(identifier, *, api_key):
        openalex_calls.append(identifier)

    result = await PaperLookupService(repository, openalex_lookup).lookup(
        PaperLookup(
            ["openalex:W77", "arxiv:2403.00001", "doi:10.1234/missing", "openalex:W77"]
        )
    )

    assert [item.status for item in result.items] == [
        LookupStatus.MISSING_API_KEY,
        LookupStatus.NOT_FOUND,
        LookupStatus.NOT_FOUND,
        LookupStatus.MISSING_API_KEY,
    ]
    assert repository.calls == [
        [
            PaperIdentifier(IdentifierKind.ARXIV, "2403.00001"),
            PaperIdentifier(IdentifierKind.DOI, "10.1234/missing"),
        ]
    ]
    assert openalex_calls == []
    assert result.execution[-1].source == PaperSource.OPENALEX
    assert result.execution[-1].status == SourceExecutionStatus.FAILED


@pytest.mark.asyncio
async def test_lookup_keeps_partial_openalex_provider_errors_and_continues():
    request = httpx.Request("GET", "https://api.openalex.org/works/W1")
    too_many_requests = httpx.HTTPStatusError(
        "rate limited", request=request, response=httpx.Response(429, request=request)
    )
    too_many_requests = ProviderError(str(too_many_requests))
    timeout = ProviderError("timed out")
    repository = FakePaperRepository()
    resolved = make_paper(PaperSource.OPENALEX, title="Third result")
    calls = []
    outcomes = iter([too_many_requests, timeout, resolved])

    async def openalex_lookup(identifier, *, api_key):
        calls.append(identifier)
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    result = await PaperLookupService(repository, openalex_lookup).lookup(
        PaperLookup(["openalex:W1", "openalex:W2", "openalex:W3"]),
        api_key="request-key",
    )

    assert [item.status for item in result.items] == [
        LookupStatus.FAILED,
        LookupStatus.FAILED,
        LookupStatus.FOUND,
    ]
    assert [item.error for item in result.items] == [
        str(too_many_requests),
        str(timeout),
        None,
    ]
    assert result.execution[0].status == SourceExecutionStatus.PARTIAL
    assert result.execution[0].error == str(too_many_requests)
    assert len(calls) == 3
    assert repository.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identifiers", [["openalex:W1"], ["openalex:W1", "openalex:W2"]]
)
async def test_single_and_all_failed_openalex_calls_reraise_first_provider_error(
    identifiers,
):
    first_error = ProviderError("OpenAlex 429 response")
    calls = []

    async def openalex_lookup(identifier, *, api_key):
        calls.append(identifier)
        raise first_error

    with pytest.raises(ProviderError) as caught:
        await PaperLookupService(FakePaperRepository(), openalex_lookup).lookup(
            PaperLookup(identifiers), api_key="request-key"
        )

    assert caught.value is first_error
    assert str(caught.value) == "OpenAlex 429 response"
    assert calls == [
        PaperIdentifier(IdentifierKind.OPENALEX, identifier.rsplit(":", 1)[1])
        for identifier in identifiers
    ]


@pytest.mark.asyncio
async def test_single_doi_local_miss_then_openalex_error_reraises_provider_error():
    doi = PaperIdentifier(IdentifierKind.DOI, "10.1234/missing")
    error = ProviderError("OpenAlex 429 response")
    repository = FakePaperRepository()

    async def openalex_lookup(identifier, *, api_key):
        raise error

    with pytest.raises(ProviderError) as caught:
        await PaperLookupService(repository, openalex_lookup).lookup(
            PaperLookup([f"doi:{doi.value}"]), api_key="request-key"
        )

    assert caught.value is error
    assert repository.calls == [[doi]]


@pytest.mark.asyncio
async def test_all_local_msearch_errors_raise_provider_error_with_original_text():
    arxiv = PaperIdentifier(IdentifierKind.ARXIV, "2401.00001")
    doi = PaperIdentifier(IdentifierKind.DOI, "10.1234/blocked")
    repository = FakePaperRepository(
        {
            arxiv: PaperLookupMatch([], error="arxiv shard unavailable"),
            doi: PaperLookupMatch([], error="doi shard unavailable"),
        }
    )

    with pytest.raises(ProviderError, match="arxiv shard unavailable"):
        await PaperLookupService(repository).lookup(
            PaperLookup([arxiv.value, f"doi:{doi.value}"])
        )


@pytest.mark.asyncio
async def test_local_msearch_error_is_partial_when_peer_is_confirmed_not_found():
    failed = PaperIdentifier(IdentifierKind.ARXIV, "2401.00001")
    missing = PaperIdentifier(IdentifierKind.DOI, "10.1234/missing")
    repository = FakePaperRepository(
        {failed: PaperLookupMatch([], error="one msearch item failed")}
    )

    result = await PaperLookupService(repository).lookup(
        PaperLookup([failed.value, f"doi:{missing.value}"])
    )

    assert [item.status for item in result.items] == [
        LookupStatus.FAILED,
        LookupStatus.NOT_FOUND,
    ]
    assert result.items[0].error == "one msearch item failed"
    assert result.execution[0].status == SourceExecutionStatus.PARTIAL
    assert result.execution[0].error == "one msearch item failed"


@pytest.mark.asyncio
async def test_openalex_error_is_partial_when_peer_is_confirmed_not_found():
    error = ProviderError("temporary OpenAlex failure")
    calls = []

    async def openalex_lookup(identifier, *, api_key):
        calls.append(identifier)
        if identifier.value == "W1":
            raise error

    result = await PaperLookupService(FakePaperRepository(), openalex_lookup).lookup(
        PaperLookup(["openalex:W1", "openalex:W2"]), api_key="request-key"
    )

    assert [item.status for item in result.items] == [
        LookupStatus.FAILED,
        LookupStatus.NOT_FOUND,
    ]
    assert result.items[0].error == str(error)
    assert result.execution[0].status == SourceExecutionStatus.PARTIAL
    assert result.execution[0].error == str(error)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_repository_batch_exception_propagates_unchanged():
    failure = RuntimeError("repository msearch failed")

    class FailingRepository:
        async def lookup(self, identifiers):
            raise failure

    with pytest.raises(RuntimeError) as caught:
        await PaperLookupService(FailingRepository()).lookup(
            PaperLookup(["arxiv:2401.00001"])
        )

    assert caught.value is failure


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [ValueError("bad provider contract"), asyncio.CancelledError()]
)
async def test_program_error_and_cancellation_propagate(failure):
    async def openalex_lookup(identifier, *, api_key):
        raise failure

    with pytest.raises(type(failure)) as caught:
        await PaperLookupService(FakePaperRepository(), openalex_lookup).lookup(
            PaperLookup(["openalex:W1"]), api_key="request-key"
        )

    assert caught.value is failure


@pytest.mark.parametrize("purpose", [LookupPurpose.ENRICH, LookupPurpose.FULL])
@pytest.mark.asyncio
async def test_lookup_leaves_enrich_and_full_to_s2_stage(purpose):
    repository = FakePaperRepository()
    openalex_calls = []

    async def openalex_lookup(identifier, *, api_key):
        openalex_calls.append(identifier)

    with pytest.raises(ServiceException):
        await PaperLookupService(repository, openalex_lookup).lookup(
            PaperLookup(["openalex:W77"], purpose=purpose), api_key="request-key"
        )

    assert repository.calls == []
    assert openalex_calls == []
