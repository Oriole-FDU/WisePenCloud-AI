from __future__ import annotations

import pytest

from chat.application.tools.web_tools.web_fetch.coordinator import (
    FetchCoordinator,
)
from chat.application.tools.web_tools.common import WebContentCacheValue
from chat.application.tools.web_tools.web_fetch.core.models import RawFetchOutput


class _MemoryCacheRepository:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], WebContentCacheValue] = {}

    async def get_value(
        self,
        *,
        url: str,
        cache_variant: str = "",
    ) -> WebContentCacheValue | None:
        return self.values.get((url, cache_variant))

    async def set_value(self, value: WebContentCacheValue) -> None:
        self.values[(value.canonical_url, value.cache_variant)] = value


class _StaticFetcher:
    def __init__(self, *, pdf: bool = False) -> None:
        self.pdf = pdf
        self.calls = 0

    async def fetch(self, url: str) -> RawFetchOutput:
        self.calls += 1
        return RawFetchOutput(
            source_url=url,
            raw_html=None if self.pdf else "article content",
            pdf_bytes=b"%PDF-fake" if self.pdf else None,
        )


async def _unused_fetch(_: str) -> RawFetchOutput:
    raise AssertionError("browser fallback should not run")


class _BrowserFetcher:
    fetch = staticmethod(_unused_fetch)


@pytest.mark.asyncio
async def test_html_result_hits_url_cache_on_second_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "chat.application.tools.web_tools.web_fetch.coordinator.clean_html",
        lambda raw_html, *, url=None: raw_html,
    )
    static_fetcher = _StaticFetcher()
    coordinator = FetchCoordinator(
        static_fetcher=static_fetcher,
        stealthy_fetcher=_BrowserFetcher(),
        content_cache_repository=_MemoryCacheRepository(),
        min_text_length=1,
    )

    first = await coordinator.fetch(["https://example.test/page"])
    second = await coordinator.fetch(["https://example.test/page"])

    assert static_fetcher.calls == 1
    assert first[0].is_md is True
    assert second == first


@pytest.mark.asyncio
async def test_pdf_cache_preserves_markdown_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def extract_pdf_markdown(_: bytes, *, url: str) -> str:
        return f"## Markdown from {url}"

    monkeypatch.setattr(
        "chat.application.tools.web_tools.web_fetch.coordinator.extract_pdf_markdown",
        extract_pdf_markdown,
    )
    static_fetcher = _StaticFetcher(pdf=True)
    coordinator = FetchCoordinator(
        static_fetcher=static_fetcher,
        stealthy_fetcher=_BrowserFetcher(),
        content_cache_repository=_MemoryCacheRepository(),
    )

    first = await coordinator.fetch(["https://example.test/paper.pdf"])
    second = await coordinator.fetch(["https://example.test/paper.pdf"])

    assert static_fetcher.calls == 1
    assert first[0].is_md is True
    assert second[0].is_md is True
