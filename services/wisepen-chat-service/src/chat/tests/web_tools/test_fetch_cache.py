from __future__ import annotations

import pytest

from chat.application.tools.web_tools.services.fetch.coordinator import (
    FetchCoordinator,
)
from chat.application.tools.web_tools.services.fetch.core.models import (
    RawFetchOutput,
    WebContentCacheMode,
    WebContentCacheValue,
)


class _MemoryCacheRepository:
    def __init__(self) -> None:
        self.values: dict[
            tuple[str, str, WebContentCacheMode],
            WebContentCacheValue,
        ] = {}

    async def get_value(
        self,
        *,
        user_id: str,
        url: str,
        cache_mode: WebContentCacheMode,
    ) -> WebContentCacheValue | None:
        return self.values.get((user_id, url, cache_mode)) or self.values.get(
            ("", url, cache_mode)
        )

    async def set_value(self, value: WebContentCacheValue) -> None:
        user_id = (
            ""
            if value.cache_mode is WebContentCacheMode.PUBLIC
            else value.user_id
        )
        self.values[(user_id, value.canonical_url, value.cache_mode)] = value


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
        "chat.application.tools.web_tools.services.fetch.coordinator.clean_html",
        lambda raw_html, *, url=None: raw_html,
    )
    static_fetcher = _StaticFetcher()
    coordinator = FetchCoordinator(
        static_fetcher=static_fetcher,
        stealthy_fetcher=_BrowserFetcher(),
        content_cache_repository=_MemoryCacheRepository(),
        min_text_length=1,
    )

    first = await coordinator.fetch(
        ["https://example.test/page"],
        user_id="u1",
    )
    second = await coordinator.fetch(
        ["https://example.test/page"],
        user_id="u1",
    )

    assert static_fetcher.calls == 1
    assert first[0].is_md is True
    assert second == first


@pytest.mark.asyncio
async def test_pdf_cache_preserves_plain_text_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def extract_pdf_text(_: bytes, *, url: str) -> str:
        return f"plain text from {url}"

    monkeypatch.setattr(
        "chat.application.tools.web_tools.services.fetch.coordinator.extract_pdf_text",
        extract_pdf_text,
    )
    static_fetcher = _StaticFetcher(pdf=True)
    coordinator = FetchCoordinator(
        static_fetcher=static_fetcher,
        stealthy_fetcher=_BrowserFetcher(),
        content_cache_repository=_MemoryCacheRepository(),
    )

    first = await coordinator.fetch(
        ["https://example.test/paper.pdf"],
        user_id="u1",
    )
    second = await coordinator.fetch(
        ["https://example.test/paper.pdf"],
        user_id="u1",
    )

    assert static_fetcher.calls == 1
    assert first[0].is_md is False
    assert second[0].is_md is False
