from __future__ import annotations

import pytest

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentIndex,
    ToolContentPutStatus,
    ToolContentStore,
)


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
async def test_markdown_store_projects_page_and_section_locators() -> None:
    repository = _RepositoryStub()
    result = await ToolContentStore(repository=repository).put(
        session_id="session-1",
        content_type="Text/Markdown; charset=utf-8",
        text="<!-- page 1 -->\n\n# 鉴权\n\n请求必须携带 AppBuilder API Key。",
    )

    assert result.status == ToolContentPutStatus.STORED
    assert result.receipt is not None
    assert result.receipt.total_length == len("<!-- page 1 -->\n\n# 鉴权\n\n请求必须携带 AppBuilder API Key。")
    assert repository.stored is not None
    assert repository.stored.chunks[0].page_labels == ("1",)
    assert repository.stored.chunks[0].section_paths == (("鉴权",),)
    assert result.receipt.supported_selectors == (
        "chunk_indices",
        "block_kinds",
        "sections",
        "page_labels",
    )
    assert repository.stored.index is not None
    assert any(
        entry.locator_name == "page:1"
        and entry.page_label == "1"
        and entry.chunk_indices == (0,)
        for entry in repository.stored.index.entries
    )


@pytest.mark.asyncio
async def test_markdown_store_routes_each_marked_page_to_one_chunk() -> None:
    repository = _RepositoryStub()
    result = await ToolContentStore(repository=repository).put(
        session_id="session-1",
        text=(
            "<!-- page 1 -->\n\n# 第一页\n\n第一段。\n\n## 第二节\n\n第二段。\n\n"
            "<!-- page 2 -->\n\n# 第二页\n\n第三段。"
        ),
    )

    assert result.status == ToolContentPutStatus.STORED
    assert repository.stored is not None
    assert len(repository.stored.chunks) == 2
    assert [chunk.page_labels for chunk in repository.stored.chunks] == [
        ("1",),
        ("2",),
    ]


@pytest.mark.asyncio
async def test_markdown_store_keeps_nested_section_and_table_anchor() -> None:
    repository = _RepositoryStub()
    result = await ToolContentStore(repository=repository).put(
        session_id="session-1",
        text=(
            "<!-- page 4 -->\n\n"
            "# 一级\n\n"
            "## 二级\n\n"
            "· Table 1: 指标说明\n\n"
            "| A | B |\n"
            "|---|---|\n"
            "| 1 | 2 |"
        ),
    )

    assert result.status == ToolContentPutStatus.STORED
    assert repository.stored is not None
    table_chunk = next(
        chunk for chunk in repository.stored.chunks if "table" in chunk.block_kinds
    )
    assert table_chunk.section_paths == (("一级",), ("一级", "二级"))
    assert table_chunk.page_labels == ("4",)
    assert table_chunk.anchor_labels == ("Table 1",)


@pytest.mark.asyncio
async def test_plain_text_store_uses_plain_text_chunker() -> None:
    repository = _RepositoryStub()
    result = await ToolContentStore(repository=repository).put(
        session_id="session-1",
        content_type="text/plain",
        text="普通文本内容。",
    )

    assert result.status == ToolContentPutStatus.STORED
    assert repository.stored is not None
    assert repository.stored.index == ToolContentIndex()
    assert "sections" not in result.receipt.supported_selectors
    assert "page_labels" not in result.receipt.supported_selectors
    assert "anchor_labels" not in result.receipt.supported_selectors


@pytest.mark.asyncio
async def test_store_preserves_metadata_on_storage_and_receipt() -> None:
    repository = _RepositoryStub()
    result = await ToolContentStore(repository=repository).put(
        session_id="session-1",
        text="来源文本。",
        metadata={"source_url": "https://example.com"},
    )

    assert result.status == ToolContentPutStatus.STORED
    assert result.receipt is not None
    assert result.receipt.metadata == {"source_url": "https://example.com"}
    assert repository.stored is not None
    assert repository.stored.metadata == {"source_url": "https://example.com"}


@pytest.mark.asyncio
async def test_store_preserves_raw_text_for_chunk_offsets() -> None:
    repository = _RepositoryStub()
    text = "\n<!-- page 1 -->\n\n# 鉴权\n\n正文。\n"

    await ToolContentStore(repository=repository).put(
        session_id="session-1",
        text=text,
    )

    assert repository.stored is not None
    assert repository.stored.text == text
    assert repository.stored.chunks[0].start_offset is not None
    assert repository.stored.chunks[0].start_offset > 0


@pytest.mark.asyncio
async def test_store_distinguishes_empty_and_too_large_text() -> None:
    repository = _RepositoryStub()
    store = ToolContentStore(repository=repository, max_chars=3)

    empty = await store.put(session_id="session-1", text=" \n\t ")
    too_large = await store.put(session_id="session-1", text="xxxx")

    assert empty.status == ToolContentPutStatus.EMPTY_TEXT
    assert too_large.status == ToolContentPutStatus.CONTENT_TOO_LARGE
    assert repository.stored is None


def test_store_rejects_invalid_max_chars() -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        ToolContentStore(repository=_RepositoryStub(), max_chars=0)
