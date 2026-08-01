from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from chat.application.tools.web_tools.common import (
    WebContentCache,
    WebContentCacheValue,
)


class _RecordingRepository:
    def __init__(self) -> None:
        self.value: WebContentCacheValue | None = None

    async def get_value(
        self,
        *,
        url: str,
        cache_variant: str = "",
    ) -> WebContentCacheValue | None:
        del url, cache_variant
        return None

    async def set_value(self, value: WebContentCacheValue) -> None:
        self.value = value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected_ttl"),
    [
        ({}, timedelta(hours=2)),
        ({"cache-control": "max-age=60"}, timedelta(seconds=60)),
        ({"cache-control": "max-age=999999"}, timedelta(days=1)),
        ({"cache-control": "no-cache"}, timedelta(0)),
    ],
)
async def test_cache_write_respects_http_freshness(
    headers: dict[str, str],
    expected_ttl: timedelta,
) -> None:
    repository = _RecordingRepository()
    before = datetime.now(timezone.utc)

    await _write_result(repository, headers=headers)

    after = datetime.now(timezone.utc)
    assert repository.value is not None
    assert before + expected_ttl <= repository.value.expire_at
    assert repository.value.expire_at <= after + expected_ttl


@pytest.mark.asyncio
async def test_cache_write_skips_no_store_response() -> None:
    repository = _RecordingRepository()

    await _write_result(
        repository,
        headers={"cache-control": "no-store"},
    )

    assert repository.value is None


async def _write_result(
    repository: _RecordingRepository,
    *,
    headers: dict[str, str],
) -> None:
    url = "https://example.test/page"
    cache = WebContentCache(repository=repository)
    await cache.write(
        url=url,
        headers=headers,
        text="content",
        is_md=True,
        raw_html="<article>content</article>",
    )
