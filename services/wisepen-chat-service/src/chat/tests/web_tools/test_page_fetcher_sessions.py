from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat.application.tools.web_tools.web_fetch.fetchers.static_page_fetcher import (
    StaticPageFetcher,
)
from chat.application.tools.web_tools.web_fetch.fetchers.stealthy_page_fetcher import (
    StealthyPageFetcher,
)


@pytest.mark.asyncio
async def test_static_page_fetcher_uses_injected_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def validate(url: str) -> str:
        return url

    monkeypatch.setattr(
        "chat.application.tools.web_tools.web_fetch.fetchers.static_page_fetcher.validate_public_http_url_async",
        validate,
    )
    session = _FakeStaticSession()
    fetcher = StaticPageFetcher(session=session)

    first = await fetcher.fetch("https://example.com/one")
    second = await fetcher.fetch("https://example.com/two")

    assert first.source_url == "https://example.com/one"
    assert second.source_url == "https://example.com/two"
    assert session.calls == [
        "https://example.com/one",
        "https://example.com/two",
    ]


@pytest.mark.asyncio
async def test_stealthy_page_fetcher_uses_injected_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def validate(url: str) -> str:
        return url

    monkeypatch.setattr(
        "chat.application.tools.web_tools.web_fetch.fetchers.stealthy_page_fetcher.validate_public_http_url_async",
        validate,
    )
    session = _FakeStealthySession()
    fetcher = StealthyPageFetcher(session=session)

    first = await fetcher.fetch("https://example.com/one")
    second = await fetcher.fetch("https://example.com/two")

    assert first.source_url == "https://example.com/one"
    assert second.source_url == "https://example.com/two"
    assert session.calls == [
        "https://example.com/one",
        "https://example.com/two",
    ]


class _FakeStaticSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(self, url: str, **_: object) -> SimpleNamespace:
        self.calls.append(url)
        return _response(url)


class _FakeStealthySession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, url: str, **_: object) -> SimpleNamespace:
        self.calls.append(url)
        return _response(url)


def _response(url: str) -> SimpleNamespace:
    return SimpleNamespace(
        status=200,
        body=b"<html><body>content</body></html>",
        headers={"content-type": "text/html"},
        history=(),
        url=url,
        encoding="utf-8",
    )
