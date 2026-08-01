from __future__ import annotations

from typing import Any

import pytest

from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.output.cache import ToolOutputCache
from chat.application.tools.core.output.tool_return import CacheableText, ToolReturn
from chat.application.tools.web_tools import WebCrawlTool, WebFetchTool
from chat.application.tools.web_tools.web_fetch.content import pdf_extract
from chat.application.tools.web_tools.web_fetch.core.errors import UrlFetchError
from chat.application.tools.web_tools.web_fetch.core.models import (
    WebFetchResult,
)


class _FetchCoordinator:
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
                    text="## PDF",
                    is_md=True,
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
async def test_web_fetch_preserves_html_and_pdf_markdown_formats(
) -> None:
    result = await WebFetchTool(
        fetch_coordinator=_FetchCoordinator(),
    ).execute(
        {},
        urls=["https://example.com", "https://example.com/paper.pdf"],
    )

    assert [content.is_md for content in result.cacheable_texts] == [True, True]
    assert [content.metadata for content in result.cacheable_texts] == [
        {"source_url": "https://example.com"},
        {"source_url": "https://example.com/paper.pdf"},
    ]
    assert result.visible_result["items"] == (
        {
            "source_url": "https://example.com",
        },
        {
            "source_url": "https://example.com/paper.pdf",
        },
    )


@pytest.mark.asyncio
async def test_web_crawl_marks_cleaned_html_as_markdown() -> None:
    result = await WebCrawlTool(crawler=_Crawler()).execute(
        {},
        seed_url="https://example.com",
    )

    assert len(result.cacheable_texts) == 1
    assert result.cacheable_texts[0].is_md is True
    assert result.cacheable_texts[0].metadata == {
        "source_url": "https://example.com"
    }


@pytest.mark.asyncio
async def test_pdf_extraction_runs_inspector_in_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, object] = {}

    async def fake_to_thread(function: Any, content: bytes) -> Any:
        called["function"] = function
        called["content"] = content
        page = type("PdfPage", (), {"page": 0, "markdown": "## PDF\n"})()
        return type("PdfResult", (), {"pages": (page,)})()

    monkeypatch.setattr(pdf_extract.asyncio, "to_thread", fake_to_thread)

    markdown = await pdf_extract.extract_pdf_markdown(
        b"%PDF-1.7 fake",
        url="https://example.com/paper.pdf",
    )

    assert markdown == "<!-- page 1 -->\n\n## PDF"
    assert called == {
        "function": pdf_extract.pdf_inspector.extract_pages_markdown_bytes,
        "content": b"%PDF-1.7 fake",
    }


@pytest.mark.asyncio
async def test_pdf_extraction_rejects_empty_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_to_thread(*_: Any) -> Any:
        page = type("PdfPage", (), {"page": 0, "markdown": ""})()
        return type("PdfResult", (), {"pages": (page,)})()

    monkeypatch.setattr(pdf_extract.asyncio, "to_thread", fake_to_thread)

    with pytest.raises(UrlFetchError, match="no extractable markdown"):
        await pdf_extract.extract_pdf_markdown(
            b"%PDF-1.7 fake",
            url="https://example.com/paper.pdf",
        )


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
                CacheableText(
                    text="# html",
                    is_md=True,
                    metadata={"source_url": "https://example.com"},
                ),
                CacheableText(
                    text="pdf",
                    is_md=False,
                    metadata={"source_url": "https://example.com/paper.pdf"},
                ),
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
