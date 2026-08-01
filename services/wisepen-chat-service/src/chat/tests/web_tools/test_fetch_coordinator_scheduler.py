from __future__ import annotations

import asyncio

import pytest

from chat.application.tools.utils.url import UrlSecurityError
from chat.application.tools.web_tools.web_fetch.core.errors import (
    UrlFetchNetworkError,
)
from chat.application.tools.web_tools.web_fetch.coordinator import FetchCoordinator
from chat.application.tools.web_tools.web_fetch.core.models import RawFetchOutput


@pytest.mark.asyncio
async def test_fetch_releases_file_sniff_worker_when_scrapling_page_fetch_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "chat.application.tools.web_tools.web_fetch.coordinator.clean_html",
        lambda raw_html, *, url=None: raw_html,
    )
    scrapling_started = asyncio.Event()
    release_scrapling = asyncio.Event()
    fast_sniff_seen = asyncio.Event()
    static_fetcher = _SchedulerStaticFetcher(fast_fetch_seen=fast_sniff_seen)
    stealthy_fetcher = _BlockingScraplingFetcher(
        started=scrapling_started,
        release=release_scrapling,
    )
    coordinator = FetchCoordinator(
        static_fetcher=static_fetcher,
        stealthy_fetcher=stealthy_fetcher,
        batch_concurrency=1,
        min_text_length=40,
    )

    fetch_task = asyncio.create_task(
        coordinator.fetch(
            [
                "https://example.test/slow",
                "https://example.test/fast",
            ],
        )
    )

    await asyncio.wait_for(scrapling_started.wait(), timeout=1)
    await asyncio.wait_for(fast_sniff_seen.wait(), timeout=1)
    assert not fetch_task.done()

    release_scrapling.set()
    result = await asyncio.wait_for(fetch_task, timeout=1)

    assert [item.source_url for item in result] == [
        "https://example.test/slow",
        "https://example.test/fast",
    ]
    assert static_fetcher.calls == [
        "https://example.test/slow",
        "https://example.test/fast",
    ]


@pytest.mark.asyncio
async def test_fetch_allows_each_failed_static_fetch_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "chat.application.tools.web_tools.web_fetch.coordinator.clean_html",
        lambda raw_html, *, url=None: raw_html,
    )
    stealthy_fetcher = _CountingScraplingFetcher()
    coordinator = FetchCoordinator(
        static_fetcher=_AlwaysFailingStaticFetcher(),
        stealthy_fetcher=stealthy_fetcher,
        batch_concurrency=2,
        min_text_length=40,
    )

    result = await coordinator.fetch(
        [
            "https://example.test/one",
            "https://example.test/two",
        ],
    )

    assert len(result) == 2
    assert sorted(stealthy_fetcher.calls) == [
        "https://example.test/one",
        "https://example.test/two",
    ]


@pytest.mark.asyncio
async def test_fetch_skips_invalid_url_without_blocking_other_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "chat.application.tools.web_tools.web_fetch.coordinator.clean_html",
        lambda raw_html, *, url=None: raw_html,
    )
    static_fetcher = _RejectingInvalidUrlFetcher()
    coordinator = FetchCoordinator(
        static_fetcher=static_fetcher,
        stealthy_fetcher=_UnusedFetcher(),
        batch_concurrency=1,
        min_text_length=1,
    )

    result = await coordinator.fetch(
        [
            "not a URL",
            "https://example.test/valid",
        ],
    )

    assert static_fetcher.calls == [
        "not a URL",
        "https://example.test/valid",
    ]
    assert [item.source_url for item in result] == [
        "https://example.test/valid",
    ]


class _SchedulerStaticFetcher:
    def __init__(self, *, fast_fetch_seen: asyncio.Event) -> None:
        self._fast_fetch_seen = fast_fetch_seen
        self.calls: list[str] = []

    async def fetch(self, url: str) -> RawFetchOutput:
        self.calls.append(url)
        if url.endswith("/fast"):
            self._fast_fetch_seen.set()
            return _raw(url, raw_html="static content " * 10)
        raise UrlFetchNetworkError(url=url, reason="network")


class _AlwaysFailingStaticFetcher:
    async def fetch(self, url: str) -> RawFetchOutput:
        raise UrlFetchNetworkError(url=url, reason="network")


class _RejectingInvalidUrlFetcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, url: str) -> RawFetchOutput:
        self.calls.append(url)
        if url == "not a URL":
            raise UrlSecurityError("URL is malformed")
        return _raw(url, raw_html="valid content")


class _UnusedFetcher:
    async def fetch(self, url: str) -> RawFetchOutput:
        raise AssertionError(f"unexpected fallback for {url}")


class _BlockingScraplingFetcher:
    def __init__(self, *, started: asyncio.Event, release: asyncio.Event) -> None:
        self._started = started
        self._release = release

    async def fetch(self, url: str) -> RawFetchOutput:
        self._started.set()
        await self._release.wait()
        return _raw(url, raw_html="fallback content " * 10)


class _CountingScraplingFetcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, url: str) -> RawFetchOutput:
        self.calls.append(url)
        return _raw(url, raw_html="fallback content " * 10)


def _raw(url: str, *, raw_html: str) -> RawFetchOutput:
    return RawFetchOutput(
        source_url=url,
        raw_html=raw_html,
    )
