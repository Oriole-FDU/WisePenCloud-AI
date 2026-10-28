
from pdf_fakes import make_pdf_service
import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime

import orjson
import pytest
import scholar.application.services.discovery as discovery_module
from common.core.exceptions import ServiceException
from pydantic import ValidationError
from scholar.api.schemas.papers import DiscoveryRequest
from scholar.application.field_policies import FieldPolicy
from scholar.application.services.discovery import (
    PaperDiscoveryQuery,
    PaperDiscoveryService,
)
from scholar.core.storage.redis.discovery_cache import RedisDiscoveryCacheRepository
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_discoverer import (
    DiscoveryPeriod,
    DiscoverySort,
    PaperDiscovery,
)
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.models.source_execution import SourceExecutionStatus


class FakeDiscoveryCache:
    def __init__(self):
        self.values = {}
        self.get_calls = []
        self.put_calls = []

    async def get(self, key):
        self.get_calls.append(key)
        return self.values.get(key)

    async def put(self, key, discovery):
        self.put_calls.append((key, discovery))
        self.values[key] = discovery


class FakeDiscoverer:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def discover(self, *, period, sort, limit, api_key=None):
        self.calls.append((period, sort, limit))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class RedisSpy:
    def __init__(self):
        self.values = {}
        self.set_calls = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, ex):
        self.values[key] = value
        self.set_calls.append((key, value, ex))


def make_paper(arxiv_id):
    return Paper(
        arxiv_id=arxiv_id,
        doi=None,
        title=f"Paper {arxiv_id}",
        abstract="Abstract",
        authors=["Author"],
        publication_date=date(2026, 10, 27),
        year=2026,
        url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        source=PaperSource.HUGGINGFACE,
        metadata={"huggingface": {"upvotes": 1}},
    )


def service_for(field, source, discoverer, cache):
    return PaperDiscoveryService(
        discoverers={source: discoverer},
        policies={field: FieldPolicy(searchers=[], discoverer=source)},
        cache=cache,
    pdf=make_pdf_service())


class FixedDateTime(datetime):
    current = datetime(2026, 10, 27, 12, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.current


@pytest.mark.asyncio
async def test_discovery_projects_cold_and_cached_results_without_changing_facts():
    paper = make_paper("2501.12948")
    paper.authors = ["First Author", "Second Author"]
    paper.metadata["huggingface"].update({
        "ai_summary": "A compressed abstract",
        "ai_keywords": ["reinforcement learning"],
        "upvotes": 0,
        "github_repo": None,
    })
    original = deepcopy(paper)
    channel = FakeDiscoverer(PaperDiscovery([paper], None, None))
    service = service_for(
        AcademicField.COMPUTER_SCIENCE, "huggingface", channel, FakeDiscoveryCache()
    )
    query = PaperDiscoveryQuery(AcademicField.COMPUTER_SCIENCE)

    first = await service.discover(query, credentials={'huggingface': 'user-key'})
    first.papers[0].metadata["huggingface"]["ai_keywords"].append("Changed by caller")
    repeated = await service.discover(query, credentials={'huggingface': 'user-key'})

    assert repeated.papers[0].authors == ["First Author et al."]
    assert repeated.papers[0].metadata == {"huggingface": {
        "upvotes": 0, "ai_keywords": ["reinforcement learning"],
    }}
    assert repeated.execution[0].cache_hit is True
    assert len(channel.calls) == 1
    assert paper == original


@pytest.mark.asyncio
async def test_configured_policy_routes_and_returns_actual_window(monkeypatch):
    monkeypatch.setattr(discovery_module, "datetime", FixedDateTime)
    first, second = make_paper("2401.00001"), make_paper("2401.00002")
    channel = FakeDiscoverer(
        PaperDiscovery([first, second], date(2026, 10, 27), date(2026, 10, 27))
    )
    service = service_for(
        AcademicField.LAW,
        "configured-channel",
        channel,
        FakeDiscoveryCache(),
    )

    result = await service.discover(
        PaperDiscoveryQuery(
            field=AcademicField.LAW,
            period=DiscoveryPeriod.DAILY,
            sort=DiscoverySort.RECENT,
            limit=1,
        )
    , credentials={'configured-channel': 'user-key'})

    assert [paper.arxiv_id for paper in result.papers] == ["2401.00001"]
    assert (result.window_start, result.window_end) == (
        date(2026, 10, 27),
        date(2026, 10, 27),
    )
    assert channel.calls == [(DiscoveryPeriod.DAILY, DiscoverySort.RECENT, 100)]
    assert result.execution[0].source == "configured-channel"
    assert result.execution[0].cache_hit is False
    assert result.execution[0].status == SourceExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_cache_tracks_daily_weekly_and_monthly_utc_windows(monkeypatch):
    monkeypatch.setattr(discovery_module, "datetime", FixedDateTime)
    monkeypatch.setattr(
        FixedDateTime, "current", datetime(2026, 10, 27, 12, tzinfo=UTC)
    )
    daily_oct_27 = PaperDiscovery(
        [make_paper("2401.00001")], date(2026, 10, 27), date(2026, 10, 27)
    )
    daily_recent = PaperDiscovery(
        [make_paper("2401.00002")], date(2026, 10, 27), date(2026, 10, 27)
    )
    weekly_oct = PaperDiscovery(
        [make_paper("2401.00003")], date(2026, 10, 26), date(2026, 11, 1)
    )
    monthly_oct = PaperDiscovery(
        [make_paper("2401.00004")], date(2026, 10, 1), date(2026, 10, 31)
    )
    daily_oct_28 = PaperDiscovery(
        [make_paper("2401.00005")], date(2026, 10, 28), date(2026, 10, 28)
    )
    daily_nov_2 = PaperDiscovery(
        [make_paper("2401.00006")], date(2026, 11, 2), date(2026, 11, 2)
    )
    weekly_nov = PaperDiscovery(
        [make_paper("2401.00007")], date(2026, 11, 2), date(2026, 11, 8)
    )
    monthly_nov = PaperDiscovery(
        [make_paper("2401.00008")], date(2026, 11, 1), date(2026, 11, 30)
    )
    channel = FakeDiscoverer(
        daily_oct_27,
        daily_recent,
        weekly_oct,
        monthly_oct,
        daily_oct_28,
        daily_nov_2,
        weekly_nov,
        monthly_nov,
    )
    service = service_for(
        AcademicField.LAW,
        "configured-channel",
        channel,
        FakeDiscoveryCache(),
    )

    async def run(period=DiscoveryPeriod.DAILY, sort=DiscoverySort.TRENDING, limit=10):
        return await service.discover(
            PaperDiscoveryQuery(
                AcademicField.LAW, period=period, sort=sort, limit=limit
            )
        , credentials={'configured-channel': 'user-key'})

    first_daily = await run()
    repeated_daily = await run()
    await run(sort=DiscoverySort.RECENT)
    first_weekly = await run(period=DiscoveryPeriod.WEEKLY)
    repeated_weekly = await run(period=DiscoveryPeriod.WEEKLY)
    first_monthly = await run(period=DiscoveryPeriod.MONTHLY)
    repeated_monthly = await run(period=DiscoveryPeriod.MONTHLY)

    monkeypatch.setattr(
        FixedDateTime, "current", datetime(2026, 10, 28, 12, tzinfo=UTC)
    )
    next_daily = await run()
    same_week = await run(period=DiscoveryPeriod.WEEKLY)
    same_month = await run(period=DiscoveryPeriod.MONTHLY)

    monkeypatch.setattr(FixedDateTime, "current", datetime(2026, 11, 2, 12, tzinfo=UTC))
    november_daily = await run()
    november_week = await run(period=DiscoveryPeriod.WEEKLY)
    november_month = await run(period=DiscoveryPeriod.MONTHLY)

    assert first_daily.execution[0].cache_hit is False
    assert repeated_daily.execution[0].cache_hit is True
    assert first_weekly.execution[0].cache_hit is False
    assert repeated_weekly.execution[0].cache_hit is True
    assert first_monthly.execution[0].cache_hit is False
    assert repeated_monthly.execution[0].cache_hit is True
    assert next_daily.execution[0].cache_hit is False
    assert same_week.execution[0].cache_hit is True
    assert same_month.execution[0].cache_hit is True
    assert november_daily.execution[0].cache_hit is False
    assert november_week.execution[0].cache_hit is False
    assert november_month.execution[0].cache_hit is False
    assert (first_weekly.window_start, first_weekly.window_end) == (
        date(2026, 10, 26),
        date(2026, 11, 1),
    )
    assert (first_monthly.window_start, first_monthly.window_end) == (
        date(2026, 10, 1),
        date(2026, 10, 31),
    )
    assert (november_week.window_start, november_week.window_end) == (
        date(2026, 11, 2),
        date(2026, 11, 8),
    )
    assert (november_month.window_start, november_month.window_end) == (
        date(2026, 11, 1),
        date(2026, 11, 30),
    )
    assert len(channel.calls) == 8


@pytest.mark.asyncio
async def test_provider_error_propagates_and_failed_discovery_is_not_cached():
    error = ProviderError("Too Many Requests: retry after 12 seconds")
    successful = PaperDiscovery([make_paper("2401.00001")], None, None)
    channel = FakeDiscoverer(error, successful)
    cache = FakeDiscoveryCache()
    service = service_for(
        AcademicField.COMPUTER_SCIENCE,
        "huggingface",
        channel,
        cache,
    )
    query = PaperDiscoveryQuery(AcademicField.COMPUTER_SCIENCE)

    with pytest.raises(ProviderError, match="Too Many Requests"):
        await service.discover(query, credentials={'huggingface': 'user-key'})
    assert cache.put_calls == []

    result = await service.discover(query, credentials={'huggingface': 'user-key'})

    assert result.papers[0].arxiv_id == "2401.00001"
    assert result.execution[0].cache_hit is False
    assert len(cache.put_calls) == 1
    assert len(channel.calls) == 2


@pytest.mark.parametrize(
    ("field", "registered_source"),
    [
        (AcademicField.MEDICINE, None),
        (AcademicField.COMPUTER_SCIENCE, "unregistered"),
    ],
)
@pytest.mark.asyncio
async def test_missing_discovery_capability_fails_before_cache_read(
    field, registered_source
):
    cache = FakeDiscoveryCache()
    discoverers = (
        {} if registered_source is None else {"some-other-source": FakeDiscoverer()}
    )
    service = PaperDiscoveryService(
        discoverers=discoverers,
        policies={field: FieldPolicy(searchers=[], discoverer=registered_source)},
        cache=cache,
    pdf=make_pdf_service())

    with pytest.raises(ServiceException):
        await service.discover(PaperDiscoveryQuery(field), credentials={'huggingface': 'user-key'})

    assert cache.get_calls == []


@pytest.mark.asyncio
async def test_discoverer_cancellation_propagates_without_writing_cache():
    cache = FakeDiscoveryCache()
    service = service_for(
        AcademicField.COMPUTER_SCIENCE,
        "huggingface",
        FakeDiscoverer(asyncio.CancelledError()),
        cache,
    )

    with pytest.raises(asyncio.CancelledError):
        await service.discover(PaperDiscoveryQuery(AcademicField.COMPUTER_SCIENCE), credentials={'huggingface': 'user-key'})

    assert cache.put_calls == []


@pytest.mark.asyncio
async def test_redis_discovery_cache_preserves_windows_and_uses_configured_ttl():
    redis = RedisSpy()
    cache = RedisDiscoveryCacheRepository(redis, "scholar-index", ttl=900)
    discovery = PaperDiscovery(
        [make_paper("2401.00001")], date(2026, 10, 26), date(2026, 11, 1)
    )
    redis.values["scholar:discovery:compact-v1:scholar-index:weekly-key"] = orjson.dumps(
        discovery
    )

    assert await cache.get("weekly-key") is None

    await cache.put("weekly-key", discovery)
    restored = await cache.get("weekly-key")
    other_index = RedisDiscoveryCacheRepository(redis, "other-index", ttl=900)

    assert restored == discovery
    assert redis.set_calls[0][2] == 900
    assert orjson.loads(redis.set_calls[0][1])["window_start"] == "2026-10-26"
    assert await other_index.get("weekly-key") is None


def test_discovery_request_schema_has_only_registered_field_and_no_query():
    schema = DiscoveryRequest.model_json_schema()
    properties = schema["properties"]

    assert set(properties) == {"field", "period", "sort", "limit"}
    assert schema["required"] == ["field"]
    assert schema["$defs"]["DiscoveryField"]["enum"] == ["computer_science"]
    assert schema["$defs"]["DiscoveryPeriod"]["enum"] == [
        "daily",
        "weekly",
        "monthly",
    ]
    assert schema["$defs"]["DiscoverySort"]["enum"] == ["recent", "trending"]
    assert properties["period"]["default"] == "daily"
    assert properties["sort"]["default"] == "trending"
    assert properties["limit"]["default"] == 10
    assert properties["limit"]["minimum"] == 1
    assert properties["limit"]["maximum"] == 15
    with pytest.raises(ValidationError):
        DiscoveryRequest.model_validate(
            {"field": "computer_science", "query": "not part of discovery"}
        )


@pytest.mark.asyncio
async def test_discovery_candidate_cache_reused_across_limits_and_credentials():
    papers = [make_paper(f"2401.{index:05d}") for index in range(10)]
    channel = FakeDiscoverer(PaperDiscovery(papers, None, None))
    cache = FakeDiscoveryCache()
    service = PaperDiscoveryService(
        {"huggingface": channel},
        {
            AcademicField.COMPUTER_SCIENCE: FieldPolicy(
                searchers=[], discoverer="huggingface"
            )
        },
        cache,
    pdf=make_pdf_service())
    small = await service.discover(
        PaperDiscoveryQuery(AcademicField.COMPUTER_SCIENCE, limit=2)
    , credentials={'huggingface': 'user-key'})
    larger = await service.discover(
        PaperDiscoveryQuery(AcademicField.COMPUTER_SCIENCE, limit=8)
    , credentials={'huggingface': 'user-key'})
    assert len(small.papers) == 2
    assert len(larger.papers) == 8
    assert larger.execution[0].cache_hit
    assert len(channel.calls) == 1
    byok = await service.discover(
        PaperDiscoveryQuery(AcademicField.COMPUTER_SCIENCE, limit=8),
        credentials={"huggingface": "user"},
    )
    without_credentials = service_for(
        AcademicField.COMPUTER_SCIENCE, "huggingface", channel, cache
    )
    reads = len(cache.get_calls)
    with pytest.raises(ServiceException, match="BYOK"):
        await without_credentials.discover(PaperDiscoveryQuery(AcademicField.COMPUTER_SCIENCE, limit=8))
    assert len(cache.get_calls) == reads
    assert byok.execution[0].cache_hit
    assert byok.papers == larger.papers
    assert len(channel.calls) == 1
