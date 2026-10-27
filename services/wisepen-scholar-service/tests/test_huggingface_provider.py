import asyncio
from datetime import UTC, date, datetime

import httpx
import pytest

import scholar.providers.huggingface as huggingface_provider
from scholar.domain.interfaces.paper_discoverer import (
    DiscoveryPeriod,
    DiscoverySort,
)
from scholar.domain.interfaces.paper_enricher import PaperEnrichment
from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    parse_identifier,
)
from scholar.domain.models.paper import PaperSource
from scholar.providers.huggingface import HuggingFaceProvider


def paper_info_payload(arxiv_id="2401.00001"):
    return {
        "id": arxiv_id,
        "title": "A research paper",
        "publishedAt": "2026-10-25T12:00:00Z",
        "submittedOnDailyAt": "2026-10-24T12:00:00Z",
        "upvotes": 0,
        "githubRepo": "",
        "githubStars": False,
        "linkedModels": [{"id": "org/model", "likes": 0}],
        "numTotalModels": 1,
        "linkedDatasets": [],
        "numTotalDatasets": None,
        "paper": {
            "id": arxiv_id,
            "publishedAt": "2020-01-02T03:04:05Z",
            "submittedOnDailyAt": "2026-10-26T00:00:00Z",
            "summary": "",
            "authors": [{"name": "A. Researcher"}],
        },
    }


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_enrich_uses_exact_hf_endpoint_and_preserves_selected_json_values():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=paper_info_payload())

    async with client_for(handle) as http:
        provider = HuggingFaceProvider(http, token="hf-test-token")
        results = await provider.enrich(
            [
                parse_identifier("arxiv:2401.00001"),
                parse_identifier("doi:10.1234/not-supported-by-hf"),
            ]
        )

    result = results[0]
    assert isinstance(result, PaperEnrichment)
    assert results[1] is None
    assert len(requests) == 1
    assert requests[0].url == "https://huggingface.co/api/papers/2401.00001"
    assert dict(requests[0].url.params) == {}
    assert requests[0].headers["authorization"] == "Bearer hf-test-token"
    assert "hf-test-token" not in str(requests[0].url)
    assert result.identifiers == [PaperIdentifier(IdentifierKind.ARXIV, "2401.00001")]
    assert result.paper is None
    metadata = result.metadata["huggingface"]
    assert metadata["upvotes"] == 0
    assert metadata["github_repo"] == ""
    assert metadata["github_stars"] is False
    assert metadata["linked_models"] == [{"id": "org/model", "likes": 0}]
    assert metadata["num_total_models"] == 1
    assert metadata["linked_datasets"] == []
    assert metadata["num_total_datasets"] is None
    assert metadata["submitted_at"] == "2026-10-26T00:00:00Z"
    datetime.fromisoformat(metadata["observed_at"])


@pytest.mark.asyncio
async def test_enrich_details_maps_hf_response_to_arxiv_paper():
    async def handle(request):
        return httpx.Response(200, json=paper_info_payload())

    async with client_for(handle) as http:
        result = (
            await HuggingFaceProvider(http).enrich(
                [parse_identifier("arxiv:2401.00001")], include_details=True
            )
        )[0]

    assert result is not None
    assert result.paper is not None
    assert result.paper.source == PaperSource.HUGGINGFACE
    assert result.paper.arxiv_id == "2401.00001"
    assert result.paper.title == "A research paper"
    assert result.paper.abstract == ""
    assert result.paper.authors == ["A. Researcher"]
    assert result.paper.publication_date == date(2020, 1, 2)
    assert result.paper.year == 2020
    assert result.paper.url == "https://arxiv.org/abs/2401.00001"
    assert result.paper.pdf_url == "https://arxiv.org/pdf/2401.00001"
    assert result.paper.metadata == result.metadata


@pytest.mark.asyncio
async def test_enrich_returns_none_only_for_404_and_unsupported_identifiers():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(404)

    async with client_for(handle) as http:
        results = await HuggingFaceProvider(http).enrich(
            [
                parse_identifier("arxiv:2401.00001"),
                parse_identifier("doi:10.1234/no-arxiv-id"),
            ]
        )

    assert results == [None, None]
    assert len(requests) == 1


@pytest.mark.parametrize("status_code", [401, 429, 503])
@pytest.mark.asyncio
async def test_enrich_propagates_non_404_http_errors(status_code):
    async def handle(request):
        return httpx.Response(status_code)

    async with client_for(handle) as http:
        with pytest.raises(ProviderError) as error:
            await HuggingFaceProvider(http, token="hf-test-token").enrich(
                [parse_identifier("arxiv:2401.00001")]
            )

    assert str(error.value) == str(error.value.__cause__)
    assert isinstance(error.value.__cause__, httpx.HTTPStatusError)
    assert error.value.__cause__.response.status_code == status_code
    assert "https://huggingface.co/api/papers/2401.00001" in str(error.value)
    assert "hf-test-token" not in str(error.value)


@pytest.mark.asyncio
async def test_enrich_propagates_network_errors():
    async def handle(request):
        raise httpx.ConnectError("connection failed", request=request)

    async with client_for(handle) as http:
        with pytest.raises(ProviderError) as error:
            await HuggingFaceProvider(http).enrich(
                [parse_identifier("arxiv:2401.00001")]
            )

    assert str(error.value.__cause__) in str(error.value)
    assert "connection failed" in str(error.value)
    assert "https://huggingface.co/api/papers/2401.00001" in str(error.value)
    assert isinstance(error.value.__cause__, httpx.ConnectError)


@pytest.mark.asyncio
async def test_enrich_timeout_keeps_request_url_and_original_cause():
    async def timeout(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    async with client_for(timeout) as http:
        with pytest.raises(ProviderError) as error:
            await HuggingFaceProvider(http).enrich(
                [parse_identifier("arxiv:2401.00001")]
            )

    assert "read timed out" in str(error.value)
    assert "https://huggingface.co/api/papers/2401.00001" in str(error.value)
    assert isinstance(error.value.__cause__, httpx.ReadTimeout)


@pytest.mark.asyncio
async def test_discover_propagates_http_status_with_original_error_cause():
    async def handle(request):
        return httpx.Response(503, request=request)

    async with client_for(handle) as http:
        with pytest.raises(ProviderError) as error:
            await HuggingFaceProvider(http).discover(
                period=DiscoveryPeriod.DAILY,
                sort=DiscoverySort.RECENT,
                limit=1,
            )

    assert str(error.value) == str(error.value.__cause__)
    assert isinstance(error.value.__cause__, httpx.HTTPStatusError)
    assert "503" in str(error.value)
    assert "https://huggingface.co/api/daily_papers" in str(error.value)


@pytest.mark.asyncio
async def test_enrich_bounds_concurrent_requests_and_preserves_input_order():
    active = 0
    peak = 0

    async def handle(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        arxiv_id = request.url.path.rsplit("/", 1)[-1]
        active -= 1
        return httpx.Response(200, json=paper_info_payload(arxiv_id))

    identifiers = [parse_identifier(f"arxiv:2401.{index:05d}") for index in range(1, 6)]
    async with client_for(handle) as http:
        results = await HuggingFaceProvider(http, concurrency=2).enrich(identifiers)

    assert peak == 2
    assert [result.identifiers[0] for result in results] == identifiers


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 27, 12, tzinfo=UTC)


@pytest.mark.parametrize("sort", list(DiscoverySort))
@pytest.mark.parametrize(
    ("period", "selector", "window"),
    [
        (DiscoveryPeriod.DAILY, {}, (None, None)),
        (
            DiscoveryPeriod.WEEKLY,
            {"week": "2026-W44"},
            (date(2026, 10, 26), date(2026, 11, 1)),
        ),
        (
            DiscoveryPeriod.MONTHLY,
            {"month": "2026-10"},
            (date(2026, 10, 1), date(2026, 10, 31)),
        ),
    ],
)
@pytest.mark.asyncio
async def test_discovery_follows_official_request_and_preserves_response(
    monkeypatch, period, selector, window, sort
):
    monkeypatch.setattr(huggingface_provider, "datetime", FixedDateTime)
    requests = []
    first = paper_info_payload("2610.14224")
    first["paper"]["submittedOnDailyAt"] = "2026-10-14T00:00:00Z"
    second = paper_info_payload("2412.20138")
    del second["paper"]["submittedOnDailyAt"]
    del second["submittedOnDailyAt"]

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=[first, second])

    async with client_for(handle) as http:
        result = await HuggingFaceProvider(http).discover(
            period=period, sort=sort, limit=5
        )

    # 官方端点的较早论文和缺少渠道日期的论文均是有效结果，不据此丢弃成功响应。
    assert len(requests) == 1
    assert dict(requests[0].url.params) == {
        "p": "0",
        "limit": "5",
        "sort": "publishedAt" if sort == DiscoverySort.RECENT else "trending",
        **selector,
    }
    assert "query" not in requests[0].url.params
    assert "authorization" not in requests[0].headers
    assert [paper.arxiv_id for paper in result.papers] == ["2610.14224", "2412.20138"]
    assert (result.window_start, result.window_end) == window
    paper = result.papers[0]
    assert paper.source == PaperSource.HUGGINGFACE
    assert paper.title == "A research paper"
    assert paper.abstract == ""
    assert paper.publication_date == date(2020, 1, 2)
    assert paper.metadata["huggingface"]["submitted_at"] == "2026-10-14T00:00:00Z"
    assert result.papers[1].metadata["huggingface"]["submitted_at"] is None


@pytest.mark.asyncio
async def test_empty_daily_discovery_returns_empty_success(monkeypatch):
    monkeypatch.setattr(huggingface_provider, "datetime", FixedDateTime)

    async def handle(request):
        return httpx.Response(200, json=[])

    async with client_for(handle) as http:
        result = await HuggingFaceProvider(http).discover(
            period=DiscoveryPeriod.DAILY, sort=DiscoverySort.RECENT, limit=10
        )

    assert result.papers == []
    assert result.window_start is None
    assert result.window_end is None
