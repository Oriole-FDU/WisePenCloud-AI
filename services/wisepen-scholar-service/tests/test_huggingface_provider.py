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


@pytest.mark.asyncio
async def test_daily_discovery_uses_hf_submission_date_and_real_paper_facts(
    monkeypatch,
):
    monkeypatch.setattr(huggingface_provider, "datetime", FixedDateTime)
    requests = []
    row = paper_info_payload()
    response_rows = [[row], [row]]

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=response_rows[len(requests) - 1])

    async with client_for(handle) as http:
        result = await HuggingFaceProvider(http).discover(
            period=DiscoveryPeriod.DAILY,
            sort=DiscoverySort.RECENT,
            limit=5,
        )

    assert len(requests) == 2
    assert dict(requests[0].url.params) == {
        "p": "0",
        "limit": "1",
        "sort": "publishedAt",
    }
    assert dict(requests[1].url.params) == {
        "p": "0",
        "limit": "5",
        "sort": "publishedAt",
        "date": "2026-10-26",
    }
    assert "query" not in requests[1].url.params
    assert "authorization" not in requests[1].headers
    assert result.window_start == date(2026, 10, 26)
    assert result.window_end == date(2026, 10, 26)
    paper = result.papers[0]
    assert paper.source == PaperSource.HUGGINGFACE
    assert paper.arxiv_id == "2401.00001"
    assert paper.title == "A research paper"
    assert paper.abstract == ""
    assert paper.publication_date == date(2020, 1, 2)
    assert paper.metadata["huggingface"]["submitted_at"] == "2026-10-26T00:00:00Z"


@pytest.mark.asyncio
async def test_daily_discovery_falls_back_to_flat_submission_date(monkeypatch):
    monkeypatch.setattr(huggingface_provider, "datetime", FixedDateTime)
    row = paper_info_payload()
    del row["paper"]["submittedOnDailyAt"]
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=[row])

    async with client_for(handle) as http:
        result = await HuggingFaceProvider(http).discover(
            period=DiscoveryPeriod.DAILY,
            sort=DiscoverySort.RECENT,
            limit=1,
        )

    assert (result.window_start, result.window_end) == (
        date(2026, 10, 24),
        date(2026, 10, 24),
    )
    assert dict(requests[1].url.params)["date"] == "2026-10-24"


@pytest.mark.parametrize(
    "response_rows",
    [
        pytest.param(
            [
                [
                    {
                        **paper_info_payload(),
                        "submittedOnDailyAt": None,
                        "paper": {
                            **paper_info_payload()["paper"],
                            "submittedOnDailyAt": None,
                        },
                    },
                ]
            ],
            id="missing-submission-date",
        ),
        pytest.param(
            [
                [paper_info_payload()],
                [
                    {
                        **paper_info_payload("2401.00002"),
                        "submittedOnDailyAt": None,
                        "paper": {
                            **paper_info_payload("2401.00002")["paper"],
                            "submittedOnDailyAt": None,
                        },
                    }
                ],
            ],
            id="missing-submission-date-in-day-results",
        ),
        pytest.param(
            [
                [paper_info_payload()],
                [
                    paper_info_payload(),
                    {
                        **paper_info_payload("2401.00002"),
                        "paper": {
                            **paper_info_payload("2401.00002")["paper"],
                            "submittedOnDailyAt": "2026-10-25T00:00:00Z",
                        },
                    },
                ],
            ],
            id="mixed-submission-dates",
        ),
    ],
)
@pytest.mark.asyncio
async def test_daily_discovery_rejects_missing_or_mixed_submission_dates(
    monkeypatch, response_rows
):
    monkeypatch.setattr(huggingface_provider, "datetime", FixedDateTime)
    requests = []
    request_index = 0

    async def handle(request):
        nonlocal request_index
        requests.append(request)
        rows = (
            response_rows[request_index] if len(response_rows) > 1 else response_rows[0]
        )
        request_index += 1
        return httpx.Response(200, json=rows)

    async with client_for(handle) as http:
        with pytest.raises(ProviderError):
            await HuggingFaceProvider(http).discover(
                period=DiscoveryPeriod.DAILY,
                sort=DiscoverySort.RECENT,
                limit=2,
            )


@pytest.mark.parametrize(
    ("period", "sort", "expected_params", "start", "end"),
    [
        (
            DiscoveryPeriod.WEEKLY,
            DiscoverySort.RECENT,
            {"p": "0", "limit": "3", "sort": "publishedAt", "week": "2026-W44"},
            date(2026, 10, 26),
            date(2026, 11, 1),
        ),
        (
            DiscoveryPeriod.MONTHLY,
            DiscoverySort.RECENT,
            {"p": "0", "limit": "3", "sort": "publishedAt", "month": "2026-10"},
            date(2026, 10, 1),
            date(2026, 10, 31),
        ),
    ],
)
@pytest.mark.asyncio
async def test_periodic_discovery_sends_hf_period_and_returns_natural_window(
    monkeypatch, period, sort, expected_params, start, end
):
    monkeypatch.setattr(huggingface_provider, "datetime", FixedDateTime)
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=[paper_info_payload()])

    async with client_for(handle) as http:
        result = await HuggingFaceProvider(http).discover(
            period=period, sort=sort, limit=3
        )

    assert dict(requests[0].url.params) == expected_params
    assert len(result.papers) == 1
    assert result.window_start == start
    assert result.window_end == end


@pytest.mark.parametrize("sort", list(DiscoverySort))
@pytest.mark.parametrize(
    ("period", "outside_submission_date"),
    [
        (DiscoveryPeriod.WEEKLY, "2026-10-25T00:00:00Z"),
        (DiscoveryPeriod.MONTHLY, "2026-09-30T00:00:00Z"),
    ],
)
@pytest.mark.asyncio
async def test_periodic_discovery_rejects_rows_outside_requested_window(
    monkeypatch, period, outside_submission_date, sort
):
    monkeypatch.setattr(huggingface_provider, "datetime", FixedDateTime)
    row = paper_info_payload()
    row["paper"]["submittedOnDailyAt"] = outside_submission_date

    async def handle(request):
        return httpx.Response(200, json=[row])

    async with client_for(handle) as http:
        with pytest.raises(ProviderError):
            await HuggingFaceProvider(http).discover(
                period=period,
                sort=sort,
                limit=1,
            )


@pytest.mark.asyncio
async def test_empty_daily_discovery_has_no_assumed_window(monkeypatch):
    monkeypatch.setattr(huggingface_provider, "datetime", FixedDateTime)
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    async with client_for(handle) as http:
        result = await HuggingFaceProvider(http).discover(
            period=DiscoveryPeriod.DAILY,
            sort=DiscoverySort.RECENT,
            limit=10,
        )

    assert len(requests) == 1
    assert dict(requests[0].url.params) == {
        "p": "0",
        "limit": "1",
        "sort": "publishedAt",
    }
    assert result.window_start is None
    assert result.window_end is None


@pytest.mark.parametrize("period", list(DiscoveryPeriod))
@pytest.mark.asyncio
async def test_trending_discovery_forwards_official_period_and_sort(
    monkeypatch, period
):
    monkeypatch.setattr(huggingface_provider, "datetime", FixedDateTime)
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=[paper_info_payload()])

    async with client_for(handle) as http:
        result = await HuggingFaceProvider(http).discover(
            period=period,
            sort=DiscoverySort.TRENDING,
            limit=1,
        )

    assert len(result.papers) == 1
    params = requests[-1].url.params
    assert params["sort"] == "trending"
    selector = {
        DiscoveryPeriod.DAILY: "date",
        DiscoveryPeriod.WEEKLY: "week",
        DiscoveryPeriod.MONTHLY: "month",
    }[period]
    assert selector in params
