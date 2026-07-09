from __future__ import annotations

import pytest

from chat.application.tools.common.tool_content_store.core.models import (
    StoredToolContent,
)
from chat.application.tools.common.tool_content_store.store import ToolContentStore
from chat.application.tools.common.tool_content_store.store import (
    ToolContentPutStatus,
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


@pytest.mark.anyio
async def test_tool_content_store_projects_explicit_locator_fields() -> None:
    repository = _RepositoryStub()
    store = ToolContentStore(repository=repository)

    put_result = await store.put(
        session_id="session-1",
        text="\n\n".join(
            (
                "<!-- page 1 -->",
                "# 鉴权",
                "请求必须携带 AppBuilder API Key。",
            )
        ),
    )

    receipt = put_result.receipt
    assert put_result.status == ToolContentPutStatus.STORED
    assert receipt is not None
    assert repository.stored is not None
    assert repository.stored.chunks
    assert repository.stored.index is not None

    chunk = repository.stored.chunks[0]
    assert chunk.page_label == "1"
    page_entry = next(
        entry
        for entry in repository.stored.index.entries
        if entry.locator_kind == "page"
    )

    assert page_entry.locator_name == "page:1"
    assert page_entry.page_label == "1"
    assert page_entry.chunk_indices == (chunk.chunk_index,)


@pytest.mark.anyio
async def test_tool_content_store_preserves_nested_section_path() -> None:
    repository = _RepositoryStub()
    store = ToolContentStore(repository=repository)

    put_result = await store.put(
        session_id="session-1",
        text="# 一级\n\n## 二级\n\n正文。",
    )

    assert put_result.status == ToolContentPutStatus.STORED
    assert repository.stored is not None
    assert any(
        chunk.section_path == ("一级", "二级")
        for chunk in repository.stored.chunks
    )
    assert repository.stored.index is not None
    assert any(
        entry.locator_name == "section:一级 > 二级"
        and entry.section_path == ("一级", "二级")
        for entry in repository.stored.index.entries
    )


@pytest.mark.anyio
async def test_tool_content_store_marks_markdown_tables() -> None:
    repository = _RepositoryStub()
    store = ToolContentStore(repository=repository)

    put_result = await store.put(
        session_id="session-1",
        text="<!-- page 2 -->\n\n# 指标\n\n| A | B |\n|---|---|\n| 1 | 2 |",
    )

    assert put_result.status == ToolContentPutStatus.STORED
    assert repository.stored is not None
    table_chunk = next(
        chunk for chunk in repository.stored.chunks
        if "table" in chunk.block_kinds
    )
    assert table_chunk.page_label == "2"
    assert table_chunk.section_path == ("指标",)


@pytest.mark.anyio
async def test_tool_content_store_exposes_captioned_table_anchor() -> None:
    repository = _RepositoryStub()
    store = ToolContentStore(repository=repository)

    put_result = await store.put(
        session_id="session-1",
        text=(
            "<!-- page 4 -->\n\n"
            "·  Table 1: Maximum path lengths, per-layer complexity and minimum "
            "number of sequential operations for different layer types.\n\n"
            "|Layer Type|Complexity per Layer|Sequential|Maximum Path Length|\n"
            "|---|---|---|---|\n"
            "|||Operations||\n"
            "|Self-Attention|_O_(_n_2 _· d_)|_O_(1)|_O_(1)|\n"
        ),
    )

    assert put_result.status == ToolContentPutStatus.STORED
    assert repository.stored is not None
    table_chunk = next(
        chunk for chunk in repository.stored.chunks
        if "table" in chunk.block_kinds
    )
    assert table_chunk.page_label == "4"
    assert table_chunk.anchor_labels == ("Table 1",)


@pytest.mark.anyio
async def test_tool_content_store_put_distinguishes_empty_text() -> None:
    repository = _RepositoryStub()
    store = ToolContentStore(repository=repository)

    result = await store.put(session_id="session-1", text=" \n\t ")

    assert result.status == ToolContentPutStatus.EMPTY_TEXT
    assert result.receipt is None
    assert repository.stored is None


@pytest.mark.anyio
async def test_tool_content_store_put_distinguishes_too_large_text() -> None:
    repository = _RepositoryStub()
    store = ToolContentStore(repository=repository, max_chars=3)

    result = await store.put(
        session_id="session-1",
        text="xxxx",
    )

    assert result.status == ToolContentPutStatus.CONTENT_TOO_LARGE
    assert result.receipt is None
    assert repository.stored is None
