"""验证 OpenAlex lookup 作为通用 Paper provider 接入查询链。"""
from policy_fakes import make_policy_store

from pdf_fakes import make_pdf_service

from dataclasses import replace
from scholar.core.providers import PROVIDER_POLICIES

from datetime import date

import httpx
import pytest
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.lookup import (
    LookupStatus,
    PaperLookup,
    PaperLookupService,
)
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.identifiers import IdentifierKind, parse_identifier
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.paper import Paper, PaperSource
from scholar.core.providers import OpenAlexProvider, S2Provider, S2Transport
from scholar.core.providers.openalex import aliases
from test_s2_provider import Permits


def test_openalex_aliases_merge_id_and_supported_ids_after_normalization():
    metadata = {"openalex": {
        "id": "https://openalex.org/W123",
        "ids": {
            "openalex": "http://openalex.org/w123",
            "doi": "https://doi.org/10.5555/EXAMPLE",
            "pmid": "https://pubmed.ncbi.nlm.nih.gov/12345678",
            "pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567",
            "mag": 123456789,
        },
    }}

    assert aliases(metadata) == [
        parse_identifier("openalex:W123"),
        parse_identifier("10.5555/example"),
    ]


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
                    PaperSource.OPENALEX: LookupBinding(OpenAlexProvider(http), None, replace(PROVIDER_POLICIES["openalex"], identifier_kinds=[IdentifierKind.OPENALEX]))
                }
            )
        , pdf=make_pdf_service(), policies=make_policy_store())

        with pytest.raises(ProviderError, match="OpenAlex API key is required"):
            await lookup.lookup(
                PaperLookup(["openalex:W123"])
            )

    assert requests == []


@pytest.mark.asyncio
async def test_local_exact_match_does_not_require_openalex_key():
    requests = []
    identity = parse_identifier("2401.00001")
    base = make_paper(
        PaperSource.ARXIV,
        metadata={"arxiv": {"comments": "Accepted at a conference"}},
    )

    async def handle(request):
        requests.append(request)
        raise AssertionError("OpenAlex must reject the missing key before HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        lookup = PaperLookupService(
            LookupRunner(
                {
                    PaperSource.ARXIV: LookupBinding(LocalProvider(base), None, replace(PROVIDER_POLICIES["arxiv"], identifier_kinds=[IdentifierKind.ARXIV])),
                    PaperSource.OPENALEX: LookupBinding(OpenAlexProvider(http), None, replace(PROVIDER_POLICIES["openalex"], identifier_kinds=[IdentifierKind.ARXIV])),
                }
            )
        , pdf=make_pdf_service(), policies=make_policy_store())

        result = await lookup.lookup(
            PaperLookup([identity.value])
        )

    assert result.items[0].status == LookupStatus.FOUND
    assert result.items[0].paper.title == "Local base paper"
    assert result.items[0].paper.metadata["arxiv"]["comments"] == "Accepted at a conference"
    assert result.items[0].error is None
    assert [item.source for item in result.execution] == [PaperSource.ARXIV]
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
                    PaperSource.OPENALEX: LookupBinding(OpenAlexProvider(http), None, replace(PROVIDER_POLICIES["openalex"], identifier_kinds=[IdentifierKind.OPENALEX]))
                }
            )
        , pdf=make_pdf_service(), policies=make_policy_store())
        with pytest.raises(ProviderError) as caught:
            await lookup.lookup(
                PaperLookup(["openalex:W123"]),
                credentials={PaperSource.OPENALEX: "user-key"},
            )

    assert "503" in str(caught.value)
    assert "api.openalex.org" in str(caught.value)
    assert "user-key" not in str(caught.value)
    assert requests[0].headers["authorization"] == "Bearer user-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("openalex_found", [True, False])
async def test_s2_http_400_still_falls_back_to_openalex(openalex_found):
    requests = []
    doi = "10.1038/35035138"

    async def handle(request):
        requests.append(request)
        if request.url.host == "api.semanticscholar.org":
            return httpx.Response(400, json={"error": "No valid paper ids given"})
        assert request.url.host == "api.openalex.org"
        assert request.headers["authorization"] == "Bearer user-key"
        if not openalex_found:
            return httpx.Response(404)
        return httpx.Response(200, json={
            "id": "https://openalex.org/W123", "doi": f"https://doi.org/{doi}",
            "title": "Fallback paper", "abstract_inverted_index": None,
            "authorships": [], "publication_date": "2000-01-01", "publication_year": 2000,
            "primary_location": None, "best_oa_location": None, "locations": [],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        s2 = S2Provider(S2Transport(http, Permits(), "platform"))
        lookup = PaperLookupService(
            LookupRunner({
                "s2": LookupBinding(s2, None, PROVIDER_POLICIES["s2"]),
                "openalex": LookupBinding(OpenAlexProvider(http), None, PROVIDER_POLICIES["openalex"]),
            }),
            pdf=make_pdf_service(), policies=make_policy_store(),
        )
        # 使用真实 provider 和传输边界：S2 的 HTTP 错误必须允许后续通用来源接手。
        if openalex_found:
            result = await lookup.lookup(
                PaperLookup([doi], field=AcademicField.BIOLOGY),
                credentials={"s2": "user-s2-key", "openalex": "user-key"},
            )
            assert result.items[0].status == LookupStatus.FOUND
            assert result.items[0].paper.source == PaperSource.OPENALEX
            assert result.items[0].paper.doi == doi
            assert result.items[0].error is None
            execution = {item.source: item for item in result.execution}
            assert execution["s2"].status == "failed"
            assert "400" in execution["s2"].error
            assert execution["openalex"].status == "completed"
        else:
            # 后续来源确认未找到，不能抹掉前一来源的真实失败；最终仍传播 S2 错误。
            with pytest.raises(ProviderError) as caught:
                await lookup.lookup(
                    PaperLookup([doi], field=AcademicField.BIOLOGY),
                    credentials={"s2": "user-s2-key", "openalex": "user-key"},
                )
            assert isinstance(caught.value.__cause__, httpx.HTTPStatusError)
            assert caught.value.__cause__.response.status_code == 400

    assert [request.url.host for request in requests] == [
        "api.semanticscholar.org", "api.openalex.org",
    ]
