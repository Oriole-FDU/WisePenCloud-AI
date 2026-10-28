"""OpenAlex 的 HTTP 边界、用户凭证隔离和精确身份语义。"""

from datetime import date

import httpx
import pytest

from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import parse_identifier
from scholar.providers.openalex import OpenAlexProvider


@pytest.fixture
def work():
    return {
        "id": "https://openalex.org/W123",
        "doi": "https://doi.org/10.1234/EXAMPLE",
        "title": "A real title",
        "abstract_inverted_index": {"repeated": [0, 2], "word": [1]},
        "authorships": [{"author": {"display_name": "A. Researcher"}}],
        "publication_date": "2023-10-12",
        "publication_year": 2023,
        "primary_location": {
            "landing_page_url": "https://doi.org/10.1234/example",
            "source": {"display_name": "A journal"},
        },
        "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/2310.08560"},
        "locations": [{"landing_page_url": "https://arxiv.org/abs/2310.08560v2"}],
        "updated_date": "2026-10-01T00:00:00",
    }


@pytest.mark.asyncio
async def test_search_sends_inclusive_filters_and_current_user_bearer_only(work):
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"results": [work]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        provider = OpenAlexProvider(http)
        for key in ("user-a-key", "user-b-key"):
            papers = await provider.search(
                query="memory systems",
                field=AcademicField.COMPUTER_SCIENCE,
                published_after=date(2023, 1, 1),
                published_before=date(2023, 12, 31),
                limit=5,
                api_key=key,
            )
        assert "authorization" not in http.headers
    assert [r.headers["authorization"] for r in requests] == [
        "Bearer user-a-key",
        "Bearer user-b-key",
    ]
    assert all("api_key" not in r.url.params for r in requests)
    assert requests[0].url.params["filter"] == (
        "topics.field.id:17,from_publication_date:2023-01-01,to_publication_date:2023-12-31"
    )
    paper = papers.papers[0]
    assert paper.abstract == "repeated word repeated"
    assert paper.arxiv_id == "2310.08560"
    assert paper.doi == "10.1234/example"
    assert paper.metadata["openalex"]["id"] == work["id"]
    assert "user-b-key" not in str(paper)


@pytest.mark.asyncio
async def test_search_preserves_http_status_and_maps_network_failures():
    async def unavailable(request):
        return httpx.Response(429, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as http:
        provider = OpenAlexProvider(http)
        with pytest.raises(ProviderError) as caught:
            await provider.search(
                query="rate limit",
                field=AcademicField.GENERAL,
                published_after=None,
                published_before=None,
                limit=1,
                api_key="user-key",
            )
    assert str(caught.value) == str(caught.value.__cause__)
    assert isinstance(caught.value.__cause__, httpx.HTTPStatusError)
    assert "429" in str(caught.value)
    assert "https://api.openalex.org/works" in str(caught.value)
    assert "user-key" not in str(caught.value)

    async def offline(request):
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as http:
        provider = OpenAlexProvider(http)
        with pytest.raises(ProviderError) as caught:
            await provider.search(
                query="network failure",
                field=AcademicField.GENERAL,
                published_after=None,
                published_before=None,
                limit=1,
                api_key="user-key",
            )
    assert str(caught.value.__cause__) in str(caught.value)
    assert isinstance(caught.value.__cause__, httpx.ConnectError)
    assert "connection refused" in str(caught.value)
    assert "https://api.openalex.org/works" in str(caught.value)
    assert "user-key" not in str(caught.value)


@pytest.mark.asyncio
async def test_search_timeout_keeps_request_url_and_original_cause():
    async def timeout(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as http:
        provider = OpenAlexProvider(http)
        with pytest.raises(ProviderError) as caught:
            await provider.search(
                query="timeout",
                field=AcademicField.GENERAL,
                published_after=None,
                published_before=None,
                limit=1,
                api_key="user-key",
            )

    assert "read timed out" in str(caught.value)
    assert "https://api.openalex.org/works" in str(caught.value)
    assert "user-key" not in str(caught.value)
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)


@pytest.mark.asyncio
async def test_lookup_uses_exact_ids_and_distinguishes_missing_from_failed(work):
    requests = []

    async def handle(request):
        requests.append(request)
        if "W404" in request.url.path:
            return httpx.Response(404)
        if "W500" in request.url.path:
            return httpx.Response(500)
        return httpx.Response(200, json=work)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        provider = OpenAlexProvider(http)
        paper = await provider.lookup(
            parse_identifier("arxiv:2310.08560v1"), api_key="key"
        )
        assert paper.arxiv_id == "2310.08560"
        assert requests[-1].url.path.endswith(
            "/works/https://doi.org/10.48550/arxiv.2310.08560"
        )
        paper = await provider.lookup(
            parse_identifier("doi:10.1234/example"), api_key="key"
        )
        assert paper.doi == "10.1234/example"
        assert (
            await provider.lookup(parse_identifier("openalex:W404"), api_key="key")
            is None
        )
        with pytest.raises(ProviderError) as caught:
            await provider.lookup(parse_identifier("openalex:W500"), api_key="key")
    assert isinstance(caught.value.__cause__, httpx.HTTPStatusError)
    assert "500" in str(caught.value)
    assert "api.openalex.org" in str(caught.value)


def test_projection_keeps_missing_values_and_avoids_arbitrary_arxiv_identity(work):
    work.update(
        title=None,
        abstract_inverted_index=None,
        publication_date=None,
        publication_year=2023,
        best_oa_location=None,
    )
    work["locations"].append({"landing_page_url": "https://arxiv.org/abs/1706.03762"})
    paper = OpenAlexProvider.to_paper(work)
    assert paper.title is None and paper.abstract is None
    assert paper.publication_date is None and paper.year == 2023
    assert paper.pdf_url is None
    assert paper.arxiv_id is None
    assert paper.metadata["openalex"]["arxiv_ids"] == ["1706.03762", "2310.08560"]
