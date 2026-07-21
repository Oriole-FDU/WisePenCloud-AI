from __future__ import annotations

from typing import Any

import pytest

from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.output.cache import ToolOutputCache
from chat.application.tools.core.output.tool_return import CacheableText, ToolReturn
from chat.application.tools.web_tools import WebCrawlTool, WebFetchTool
from chat.application.tools.web_tools.services.fetch.internal import pdf_extract
from chat.application.tools.web_tools.services.fetch.core.models import (
    WebFetchResult,
)


class _FetchService:
    async def fetch(
        self,
        urls: list[str],
        **_: Any,
    ) -> tuple[WebFetchResult, ...]:
        assert urls == ["https://example.com", "https://example.com/paper.pdf"]
        return (
                WebFetchResult(
                    source_url=urls[0],
                    text="# HTML",
                    is_md=True,
                ),
                WebFetchResult(
                    source_url=urls[1],
                    text="PDF text",
                    is_md=False,
                ),
        )


class _Crawler:
    async def crawl(
        self,
        seed_url: str,
        **kwargs: Any,
    ) -> tuple[WebFetchResult, ...]:
        assert seed_url == "https://example.com"
        return (
                WebFetchResult(
                    source_url=seed_url,
                    text="# Page",
                    is_md=True,
                ),
        )


@pytest.mark.asyncio
async def test_web_fetch_preserves_html_and_pdf_cache_formats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def validate(url: str) -> str:
        return url

    monkeypatch.setattr(
        "chat.application.tools.web_tools.web_fetch_tool.validate_public_http_url_async",
        validate,
    )
    result = await WebFetchTool(service=_FetchService()).execute(
        {"user_id": "u1"},
        urls=["https://example.com", "https://example.com/paper.pdf"],
    )

    assert [content.is_md for content in result.cacheable_texts] == [True, False]
    assert result.visible_result["items"] == (
        {
            "source_url": "https://example.com",
            "content_index": 0,
        },
        {
            "source_url": "https://example.com/paper.pdf",
            "content_index": 1,
        },
    )


@pytest.mark.asyncio
async def test_web_crawl_marks_cleaned_html_as_markdown() -> None:
    result = await WebCrawlTool(crawler=_Crawler()).execute(
        {"user_id": "u1"},
        seed_url="https://example.com",
    )

    assert len(result.cacheable_texts) == 1
    assert result.cacheable_texts[0].is_md is True


@pytest.mark.asyncio
async def test_pdf_extraction_runs_pdfium_in_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, object] = {}

    async def fake_to_thread(function: Any, content: bytes) -> str:
        called["function"] = function
        called["content"] = content
        return "PDF text\n"

    monkeypatch.setattr(pdf_extract.asyncio, "to_thread", fake_to_thread)

    text = await pdf_extract.extract_pdf_text(
        b"%PDF-1.7 fake",
        url="https://example.com/paper.pdf",
    )

    assert text == "PDF text\n"
    assert called == {
        "function": pdf_extract._extract_pdf_text_sync,
        "content": b"%PDF-1.7 fake",
    }


@pytest.mark.asyncio
async def test_tool_output_cache_maps_is_md_to_content_type() -> None:
    content_types: list[str] = []

    class _Store:
        async def put(self, **kwargs: Any) -> Any:
            content_types.append(kwargs["content_type"])
            return type(
                "PutResult",
                (),
                {"receipt": None, "status": None},
            )()

    cache = ToolOutputCache(content_store=_Store(), inline_max_chars=1)
    await cache.process(
        tool_return=ToolReturn(
            cacheable_texts=(
                CacheableText(text="# html", is_md=True),
                CacheableText(text="pdf", is_md=False),
            ),
        ),
        invocation=ToolInvocation(
            tool_call_id="call-1",
            tool_name="web_fetch",
            tool_call_arguments={},
        ),
        session_id="session-1",
    )

    assert content_types == ["text/markdown", "text/plain"]
