from __future__ import annotations

import pytest

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentPutStatus,
    ToolContentStore,
)
from common.utils.chunkers import LocatorKind


class _RepositoryStub:
    def __init__(self) -> None:
        self.stored: StoredToolContent | None = None

    async def put(self, stored: StoredToolContent) -> None:
        self.stored = stored

    async def get(self, content_id: str) -> StoredToolContent | None:
        if self.stored is None or self.stored.content_id != content_id:
            return None
        return self.stored


@pytest.mark.asyncio
async def test_markdown_store_persists_semantic_chunks_and_source_locators() -> None:
    repository = _RepositoryStub()
    text = (
        "<!-- page 1 -->\n\n# 鉴权\n\n第一页正文。\n\n"
        "<!-- page 2 -->\n\n第二页正文。\n\n"
        "Figure 1: 架构。\n\n![image](figure.png)"
    )
    result = await ToolContentStore(repository=repository).put(
        session_id="session-1",
        content_type="Text/Markdown; charset=utf-8",
        text=text,
    )

    assert result.status is ToolContentPutStatus.STORED
    assert result.receipt is not None
    assert result.receipt.chunk_count == 1
    assert result.receipt.locator_count == 4
    assert result.receipt.locator_kinds == (
        LocatorKind.SECTION,
        LocatorKind.PAGE,
        LocatorKind.ANCHOR,
    )
    assert repository.stored is not None
    assert repository.stored.chunks[0].page_labels == ("1", "2")
    assert repository.stored.chunks[0].section_paths == (("鉴权",),)
    assert repository.stored.chunks[0].anchor_labels == ("Figure 1",)
    assert tuple(locator.name for locator in repository.stored.locators) == (
        "section:鉴权",
        "page:1",
        "page:2",
        "anchor:Figure 1",
    )


@pytest.mark.asyncio
async def test_plain_text_store_has_retrieval_chunks_without_locators() -> None:
    repository = _RepositoryStub()
    result = await ToolContentStore(repository=repository).put(
        session_id="session-1",
        content_type="text/plain",
        text="普通文本内容。",
    )

    assert result.receipt is not None
    assert result.receipt.locator_count == 0
    assert result.receipt.locator_kinds == ()
    assert repository.stored is not None
    assert repository.stored.chunks
    assert repository.stored.locators == ()


@pytest.mark.asyncio
async def test_store_preserves_metadata_and_authoritative_source_spans() -> None:
    repository = _RepositoryStub()
    text = "\n<!-- page 1 -->\n\n# 鉴权\n\n正文。\n"
    result = await ToolContentStore(repository=repository).put(
        session_id="session-1",
        text=text,
        metadata={"source_url": "https://example.com"},
    )

    assert result.receipt is not None
    assert result.receipt.metadata == {"source_url": "https://example.com"}
    assert repository.stored is not None
    assert repository.stored.text == text
    assert repository.stored.metadata == {"source_url": "https://example.com"}
    assert all(chunk.source_spans for chunk in repository.stored.chunks)
    assert all(
        "<!-- page" not in text[span.start_offset : span.end_offset]
        for chunk in repository.stored.chunks
        for span in chunk.source_spans
    )


@pytest.mark.asyncio
async def test_store_distinguishes_empty_and_too_large_text() -> None:
    repository = _RepositoryStub()
    store = ToolContentStore(repository=repository, max_chars=3)

    empty = await store.put(session_id="session-1", text=" \n\t ")
    too_large = await store.put(session_id="session-1", text="xxxx")

    assert empty.status is ToolContentPutStatus.EMPTY_TEXT
    assert too_large.status is ToolContentPutStatus.CONTENT_TOO_LARGE
    assert repository.stored is None


def test_store_rejects_invalid_max_chars() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        ToolContentStore(repository=_RepositoryStub(), max_chars=0)
