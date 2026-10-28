"""OpenAlex 的 HTTP 边界、用户凭证隔离和批量 lookup 语义。"""

import asyncio
from datetime import date

import httpx
import pytest
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
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
        "authorships": [
            {"author": {"display_name": "First"}},
            {"author": {"display_name": "Second"}},
        ],
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
    selected = set(requests[0].url.params["select"].split(","))
    assert "authorships" in selected
    assert not selected.intersection({"fwci", "citation_normalized_percentile"})
    paper = papers.papers[0]
    assert paper.abstract == "repeated word repeated"
    assert paper.arxiv_id == "2310.08560"
    assert paper.doi == "10.1234/example"
    assert paper.metadata["openalex"]["id"] == work["id"]
    assert paper.authors == ["First et al."]
    assert not {
        "authorships",
        "fwci",
        "citation_normalized_percentile",
        "observed_at",
    }.intersection(paper.metadata["openalex"])
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
        matches = await provider.lookup(
            [
                parse_identifier("arxiv:2310.08560v1"),
                parse_identifier("doi:10.1234/example"),
                parse_identifier("openalex:W404"),
            ],
            api_key="key",
        )
        assert matches[0].papers[0].arxiv_id == "2310.08560"
        assert matches[1].papers[0].doi == "10.1234/example"
        assert matches[2].papers == []
        assert any(
            request.url.path.endswith(
                "/works/https://doi.org/10.48550/arxiv.2310.08560"
            )
            for request in requests
        )
        with pytest.raises(ProviderError) as caught:
            await provider.lookup([parse_identifier("openalex:W500")], api_key="key")
    assert isinstance(caught.value.__cause__, httpx.HTTPStatusError)
    assert "500" in str(caught.value)
    assert "api.openalex.org" in str(caught.value)


@pytest.mark.asyncio
async def test_lookup_always_requests_details_and_returns_full_paper(work):
    requests = []

    async def handle(request):
        requests.append(request)
        fields = request.url.params["select"].split(",")
        return httpx.Response(200, json={field: work.get(field) for field in fields})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        provider = OpenAlexProvider(http)
        identifier = parse_identifier("openalex:W123")
        matches = await provider.lookup([identifier], api_key="key")

    detail_fields = {
        "title",
        "abstract_inverted_index",
        "authorships",
        "publication_date",
        "publication_year",
    }
    fields = set(requests[0].url.params["select"].split(","))
    assert {
        "id",
        "doi",
        "locations",
        "primary_location",
        "best_oa_location",
        "updated_date",
        "ids",
        "cited_by_count",
        "primary_topic",
        "open_access",
        "is_retracted",
        "mesh",
        "study_designs",
    } <= fields
    assert detail_fields <= fields
    assert isinstance(matches[0], PaperLookupMatch)
    assert len(matches[0].papers) == 1
    paper = matches[0].papers[0]
    assert paper.title == "A real title"
    assert paper.abstract == "repeated word repeated"
    assert paper.authors == ["First", "Second"]


@pytest.mark.asyncio
async def test_lookup_keeps_arxiv_id_from_normalized_doi_without_arxiv_location(work):
    work["doi"] = "https://doi.org/10.48550/arXiv.2310.08560v2"
    work["locations"] = []

    async def handle(request):
        fields = request.url.params["select"].split(",")
        return httpx.Response(200, json={field: work.get(field) for field in fields})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = (
            await OpenAlexProvider(http).lookup(
                [parse_identifier("openalex:W123")], api_key="key"
            )
        )[0]

    assert result.papers[0].arxiv_id == "2310.08560"
    assert result.papers[0].metadata["openalex"]["arxiv_ids"] == ["2310.08560"]


@pytest.mark.asyncio
async def test_lookup_limits_concurrency_and_cleans_up_cancelled_requests(work):
    active = 0
    peak = 0
    started = asyncio.Event()

    async def handle(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        started.set()
        try:
            await asyncio.sleep(0.01)
        finally:
            active -= 1
        return httpx.Response(200, json=work)

    identifiers = [parse_identifier(f"openalex:W{index}") for index in range(1, 10)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        matches = await OpenAlexProvider(http).lookup(identifiers, api_key="key")
    assert len(matches) == len(identifiers)
    assert peak <= 4

    active = 0
    cancelled = 0

    async def wait_forever(request):
        nonlocal active, cancelled
        active += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1
            cancelled += 1

    started.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(wait_forever)) as http:
        batch = asyncio.create_task(
            OpenAlexProvider(http).lookup(identifiers, api_key="key")
        )
        await started.wait()
        batch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await batch

    assert active == 0
    assert cancelled > 0


def test_projection_keeps_missing_values_and_avoids_arbitrary_arxiv_identity(work):
    work["authorships"] = [{"author": {"display_name": "A. Researcher"}}]
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
    assert paper.authors == ["A. Researcher"]
    assert paper.arxiv_id is None
    assert paper.metadata["openalex"]["arxiv_ids"] == ["1706.03762", "2310.08560"]
