"""验证 OpenAlex lookup 作为通用 Paper provider 接入查询链。"""

from datetime import date

import httpx
import pytest
from scholar.application.lookup import (
    EnrichmentStatus,
    LookupPurpose,
    LookupStatus,
    PaperLookup,
    PaperLookupService,
)
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.identifiers import IdentifierKind, parse_identifier
from scholar.domain.models.paper import Paper, PaperSource
from scholar.providers.enrichment_rules import arxiv_signals
from scholar.providers.openalex import OpenAlexProvider


def make_paper(source, *, arxiv_id="2401.00001", metadata=None):
    return Paper(
        arxiv_id=arxiv_id,
        doi=None,
        title="Local base paper",
        abstract="Local abstract",
        authors=["Local author"],
        publication_date=date(2024, 1, 2),
        year=2024,
        url="https://example.test/paper",
        pdf_url=None,
        source=source,
        metadata={} if metadata is None else metadata,
    )


class LocalProvider:
    def __init__(self, paper):
        self.paper = paper
        self.calls = []

    async def lookup(self, ids, *, api_key=None):
        ids = list(ids)
        self.calls.append((ids, api_key))
        return [PaperLookupMatch([self.paper]) for _identity in ids]


@pytest.mark.asyncio
async def test_openalex_missing_key_fails_without_sending_a_request():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        lookup = PaperLookupService(
            LookupRunner(
                {
                    PaperSource.OPENALEX: LookupBinding(
                        OpenAlexProvider(http),
                        None,
                        [IdentifierKind.OPENALEX],
                    )
                }
            )
        )

        with pytest.raises(ProviderError, match="OpenAlex API key is required"):
            await lookup.lookup(
                PaperLookup(["openalex:W123"], purpose=LookupPurpose.FULL)
            )

    assert requests == []


@pytest.mark.asyncio
async def test_openalex_missing_key_does_not_discard_a_local_base_paper():
    requests = []
    identity = parse_identifier("2401.00001")
    base = make_paper(
        PaperSource.ARXIV,
        metadata={"arxiv": {"license": "CC-BY"}},
    )

    async def handle(request):
        requests.append(request)
        raise AssertionError("OpenAlex must reject the missing key before HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        lookup = PaperLookupService(
            LookupRunner(
                {
                    PaperSource.ARXIV: LookupBinding(
                        LocalProvider(base),
                        None,
                        [IdentifierKind.ARXIV],
                        has_signals=arxiv_signals,
                    ),
                    PaperSource.OPENALEX: LookupBinding(
                        OpenAlexProvider(http),
                        None,
                        [IdentifierKind.ARXIV],
                    ),
                }
            )
        )

        result = await lookup.lookup(
            PaperLookup([identity.value], purpose=LookupPurpose.FULL)
        )

    assert result.items[0].status == LookupStatus.FOUND
    assert result.items[0].enrichment_status == EnrichmentStatus.PARTIAL
    assert result.items[0].paper.title == "Local base paper"
    assert result.items[0].paper.metadata["arxiv"]["license"] == "CC-BY"
    assert result.items[0].error == "OpenAlex API key is required"
    assert (
        next(
            item for item in result.execution if item.source == PaperSource.OPENALEX
        ).error
        == "OpenAlex API key is required"
    )
    assert requests == []


@pytest.mark.asyncio
async def test_openalex_http_failure_keeps_status_url_and_excludes_user_key():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(503, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        lookup = PaperLookupService(
            LookupRunner(
                {
                    PaperSource.OPENALEX: LookupBinding(
                        OpenAlexProvider(http),
                        None,
                        [IdentifierKind.OPENALEX],
                    )
                }
            )
        )
        with pytest.raises(ProviderError) as caught:
            await lookup.lookup(
                PaperLookup(["openalex:W123"], purpose=LookupPurpose.FULL),
                credentials={PaperSource.OPENALEX: "user-key"},
            )

    assert "503" in str(caught.value)
    assert "api.openalex.org" in str(caught.value)
    assert "user-key" not in str(caught.value)
    assert requests[0].headers["authorization"] == "Bearer user-key"
