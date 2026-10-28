import asyncio
from datetime import UTC, date, datetime

import httpx
import pytest
import scholar.providers.huggingface as huggingface_provider
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_discoverer import (
    DiscoveryPeriod,
    DiscoverySort,
)
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import (
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
async def test_hf_byok_is_request_scoped_for_search_lookup_and_discovery():
    seen = []
    payload = paper_info_payload()
    payload.update(
        ai_summary="Quick summary",
        ai_keywords=["reasoning"],
        ai_summary_model="model-name",
    )

    def handle(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(
            200, json=payload if request.url.path.endswith("2401.00001") else [payload]
        )

    async with client_for(handle) as http:
        provider = HuggingFaceProvider(http, token="platform-token")
        result = await provider.search(
            query="q",
            field=AcademicField.COMPUTER_SCIENCE,
            published_after=None,
            published_before=None,
            limit=5,
            api_key="user-token",
        )
        matches = await provider.lookup(
            [parse_identifier("2401.00001")], api_key="user-token"
        )
        discovered = await provider.discover(
            period=DiscoveryPeriod.DAILY, sort=DiscoverySort.RECENT, limit=5
        )
    assert seen == ["Bearer user-token", "Bearer user-token", "Bearer platform-token"]
    for metadata in (
        result.papers[0].metadata["huggingface"],
        matches[0].papers[0].metadata["huggingface"],
        discovered.papers[0].metadata["huggingface"],
    ):
        assert metadata["ai_summary"] == "Quick summary"
        assert metadata["ai_keywords"] == ["reasoning"]
        assert not {
            "ai_summary_model",
            "observed_at",
            "num_total_models",
            "num_total_datasets",
        }.intersection(metadata)


@pytest.mark.asyncio
async def test_search_preserves_upstream_order_and_plain_text_without_detail_requests():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "paper": {
                        "id": "2501.12948",
                        "title": "DeepSeek-R1",
                        "summary": "Plain abstract",
                        "authors": [{"name": "First"}, {"name": "Second"}],
                        "upvotes": 0,
                    },
                    "highlightedTitle": "<em>DeepSeek</em>-R1",
                },
                {
                    "paper": {
                        "id": "2501.18438",
                        "title": "Comparison",
                        "summary": "Analysis",
                    }
                },
            ],
        )

    async with client_for(handle) as http:
        result = await HuggingFaceProvider(http, token="test-token").search(
            query="DeepSeek-R1",
            field=AcademicField.COMPUTER_SCIENCE,
            published_after=None,
            published_before=None,
            limit=2,
        )
    assert [p.arxiv_id for p in result.papers] == ["2501.12948", "2501.18438"]
    assert result.papers[0].title == "DeepSeek-R1"
    assert result.papers[0].authors == ["First", "Second"]
    assert result.papers[0].metadata["huggingface"]["upvotes"] == 0
    assert len(requests) == 1
    assert requests[0].url.path == "/api/papers/search"
    assert dict(requests[0].url.params) == {"q": "DeepSeek-R1", "limit": "2"}
    assert requests[0].headers["authorization"] == "Bearer test-token"


@pytest.mark.asyncio
async def test_search_filters_bounded_candidates_by_inclusive_paper_dates():
    def handle(request):
        assert request.url.params["limit"] == "100"
        return httpx.Response(
            200,
            json=[
                {
                    "paper": {
                        "id": "2501.12948",
                        "title": "Start",
                        "publishedAt": "2025-01-22T00:00:00Z",
                    }
                },
                {
                    "paper": {
                        "id": "2501.18438",
                        "title": "End",
                        "publishedAt": "2025-01-31T00:00:00Z",
                    }
                },
                {
                    "paper": {
                        "id": "2401.00001",
                        "title": "Old",
                        "publishedAt": "2024-01-01T00:00:00Z",
                    }
                },
                {"paper": {"id": "2401.00002", "title": "Undated"}},
            ],
        )

    async with client_for(handle) as http:
        result = await HuggingFaceProvider(http).search(
            query="reasoning",
            field=AcademicField.COMPUTER_SCIENCE,
            published_after=date(2025, 1, 22),
            published_before=date(2025, 1, 31),
            limit=10,
        )
    assert [p.title for p in result.papers] == ["Start", "End"]


@pytest.mark.asyncio
async def test_lookup_uses_exact_hf_endpoint_and_preserves_selected_json_values():
    requests = []
    payload = paper_info_payload()
    payload.update(
        ai_summary_model="model-name",
        linkedModels=[
            {"id": "model-zero", "likes": 0},
            {"id": "model-tie-first", "likes": 3},
            {"id": "model-top", "likes": 8},
            {"id": "model-tie-second", "likes": 3},
            {"id": "model-missing"},
            {"id": "model-none", "likes": None},
        ],
        numTotalModels=6,
        linkedDatasets=[
            {"id": "dataset-missing"},
            {"id": "dataset-none", "likes": None},
            {"id": "dataset-zero", "likes": 0},
            {"id": "dataset-top", "likes": 2},
        ],
        numTotalDatasets=4,
    )

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    async with client_for(handle) as http:
        provider = HuggingFaceProvider(http, token="hf-test-token")
        results = await provider.lookup(
            [
                parse_identifier("arxiv:2401.00001"),
                parse_identifier("doi:10.1234/not-supported-by-hf"),
            ]
        )

    result = results[0].papers[0]
    assert results[1].papers == []
    assert len(requests) == 1
    assert requests[0].url == "https://huggingface.co/api/papers/2401.00001"
    assert dict(requests[0].url.params) == {}
    assert requests[0].headers["authorization"] == "Bearer hf-test-token"
    assert "hf-test-token" not in str(requests[0].url)
    assert result.arxiv_id == "2401.00001"
    metadata = result.metadata["huggingface"]
    assert metadata["upvotes"] == 0
    assert metadata["github_repo"] == ""
    assert metadata["github_stars"] is False
    # likes 降序取前三；缺失、None 与 0 按零分处理，同分保持上游顺序。
    assert metadata["linked_models"] == [
        {"id": "model-top", "likes": 8},
        {"id": "model-tie-first", "likes": 3},
        {"id": "model-tie-second", "likes": 3},
    ]
    assert metadata["linked_datasets"] == [
        {"id": "dataset-top", "likes": 2},
        {"id": "dataset-missing", "likes": None},
        {"id": "dataset-none", "likes": None},
    ]
    assert not {
        "ai_summary_model",
        "observed_at",
        "num_total_models",
        "num_total_datasets",
    }.intersection(metadata)
    assert metadata["submitted_at"] == "2026-10-26T00:00:00Z"


@pytest.mark.asyncio
async def test_lookup_maps_hf_response_to_complete_arxiv_paper():
    async def handle(request):
        return httpx.Response(200, json=paper_info_payload())

    async with client_for(handle) as http:
        result = (
            await HuggingFaceProvider(http).lookup(
                [parse_identifier("arxiv:2401.00001")]
            )
        )[0].papers[0]

    assert result.source == PaperSource.HUGGINGFACE
    assert result.arxiv_id == "2401.00001"
    assert result.title == "A research paper"
    assert result.abstract == ""
    assert result.authors == ["A. Researcher"]
    assert result.publication_date == date(2020, 1, 2)
    assert result.year == 2020
    assert result.url == "https://arxiv.org/abs/2401.00001"
    assert result.pdf_url == "https://arxiv.org/pdf/2401.00001"
    assert result.metadata["huggingface"]


@pytest.mark.parametrize(
    ("authors", "expected"),
    [([], []), ([{"name": "First"}, {"name": "Second"}], ["First", "Second"])],
)
@pytest.mark.asyncio
async def test_lookup_preserves_empty_and_all_authors(authors, expected):
    payload = paper_info_payload()
    payload["paper"]["authors"] = authors

    async def handle(request):
        return httpx.Response(200, json=payload)

    async with client_for(handle) as http:
        result = (
            await HuggingFaceProvider(http).lookup(
                [parse_identifier("arxiv:2401.00001")]
            )
        )[0].papers[0]

    assert result.authors == expected


@pytest.mark.asyncio
async def test_lookup_returns_empty_for_404_and_unsupported_identifiers():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(404)

    async with client_for(handle) as http:
        results = await HuggingFaceProvider(http).lookup(
            [
                parse_identifier("arxiv:2401.00001"),
                parse_identifier("doi:10.1234/no-arxiv-id"),
            ]
        )

    assert all(result.papers == [] and result.error is None for result in results)
    assert len(requests) == 1


@pytest.mark.parametrize("status_code", [401, 429, 503])
@pytest.mark.asyncio
async def test_lookup_records_non_404_http_errors_on_the_item(status_code):
    async def handle(request):
        return httpx.Response(status_code)

    async with client_for(handle) as http:
        result = await HuggingFaceProvider(http, token="hf-test-token").lookup(
            [parse_identifier("arxiv:2401.00001")]
        )

    assert result[0].papers == []
    assert str(status_code) in result[0].error
    assert "https://huggingface.co/api/papers/2401.00001" in result[0].error
    assert "hf-test-token" not in result[0].error


@pytest.mark.asyncio
async def test_lookup_records_network_errors_on_the_item():
    async def handle(request):
        raise httpx.ConnectError("connection failed", request=request)

    async with client_for(handle) as http:
        result = await HuggingFaceProvider(http).lookup(
            [parse_identifier("arxiv:2401.00001")]
        )

    assert result[0].papers == []
    assert "connection failed" in result[0].error
    assert "https://huggingface.co/api/papers/2401.00001" in result[0].error


@pytest.mark.asyncio
async def test_lookup_timeout_is_recorded_on_the_item():
    async def timeout(request):
        raise httpx.ReadTimeout("read timed out", request=request)

    async with client_for(timeout) as http:
        result = await HuggingFaceProvider(http).lookup(
            [parse_identifier("arxiv:2401.00001")]
        )

    assert "read timed out" in result[0].error
    assert "https://huggingface.co/api/papers/2401.00001" in result[0].error


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
async def test_lookup_keeps_success_beside_item_http_failure():
    async def handle(request):
        if request.url.path.endswith("2401.00002"):
            return httpx.Response(503, request=request)
        return httpx.Response(200, json=paper_info_payload("2401.00001"))

    identifiers = [
        parse_identifier("arxiv:2401.00001"),
        parse_identifier("arxiv:2401.00002"),
    ]
    async with client_for(handle) as http:
        results = await HuggingFaceProvider(http).lookup(identifiers)

    assert results[0].papers[0].arxiv_id == "2401.00001"
    assert results[0].error is None
    assert results[1].papers == []
    assert "503" in results[1].error


@pytest.mark.asyncio
async def test_lookup_propagates_invalid_response_and_cleans_up_on_cancellation():
    async def invalid_response(request):
        return httpx.Response(200, text="not-json")

    async with client_for(invalid_response) as http:
        with pytest.raises(ValueError):
            await HuggingFaceProvider(http).lookup(
                [parse_identifier("arxiv:2401.00001")]
            )

    active = 0
    started = asyncio.Event()

    async def wait_forever(request):
        nonlocal active
        active += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    identifiers = [parse_identifier(f"arxiv:2401.{index:05d}") for index in range(1, 5)]
    async with client_for(wait_forever) as http:
        batch = asyncio.create_task(
            HuggingFaceProvider(http, concurrency=2).lookup(identifiers)
        )
        await started.wait()
        batch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await batch

    assert active == 0


@pytest.mark.asyncio
async def test_lookup_bounds_concurrent_requests_and_preserves_input_order():
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
        results = await HuggingFaceProvider(http, concurrency=2).lookup(identifiers)

    assert peak == 2
    assert [result.papers[0].arxiv_id for result in results] == [
        identifier.value for identifier in identifiers
    ]


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
