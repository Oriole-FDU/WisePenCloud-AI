from __future__ import annotations

import asyncio

import pytest

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentChunk,
    ToolContentIndex,
    ToolContentIndexEntry,
)
from chat.application.tools.session_tools.tool_content_read import (
    ToolContentReadFailure,
    ToolContentRegexMatch,
    ToolContentRegexReadRequest,
    ToolContentRegexReadResult,
    ToolContentRankedExpandItem,
    ToolContentRankedExpandReadResult,
    ToolContentSelector,
    ToolContentWindow,
)
from chat.application.tools.session_tools.tool_content_read.services.chunk_selection import (
    select_chunks,
)
from chat.application.tools.session_tools.tool_content_read.services.content_window_builder import (
    ToolContentWindowBuilder,
)
from chat.application.tools.session_tools.tool_content_read.services.reader import (
    ToolContentReader,
    ToolContentRegexTimeoutError,
)
from chat.application.tools.session_tools.tool_content_read.tools import (
    ToolContentReadTool,
    ToolContentRegexReadTool,
    ToolContentRankedExpandReadTool,
)
from chat.application.utils.chunkers import SourceSpan
from chat.application.utils.ranking.pipeline import RankingPipeline


class _StoreStub:
    def __init__(self, stored: StoredToolContent | None = None) -> None:
        self._stored = stored

    async def get(
        self, *, content_id: str, session_id: str
    ) -> StoredToolContent | None:
        if self._stored is None:
            return None
        if (
            content_id != self._stored.content_id
            or session_id != self._stored.session_id
        ):
            return None
        return self._stored


class _ConcurrentStoreStub:
    def __init__(self, stored: tuple[StoredToolContent, ...]) -> None:
        self._stored = {item.content_id: item for item in stored}
        self.active = 0
        self.max_active = 0

    async def get(
        self, *, content_id: str, session_id: str
    ) -> StoredToolContent | None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            stored = self._stored.get(content_id)
            if stored is None or stored.session_id != session_id:
                return None
            return stored
        finally:
            self.active -= 1


class _RegexBatchReader:
    def __init__(self) -> None:
        self.batches: list[tuple[str, ...]] = []

    async def read_regex(
        self, *, request: ToolContentRegexReadRequest, session_id: str
    ) -> ToolContentRegexReadResult:
        self.batches.append(request.content_ids)
        if request.content_ids[0] == "cnt_16":
            raise RuntimeError("temporary failure")
        return ToolContentRegexReadResult(
            matches=tuple(
                ToolContentRegexMatch(
                    content_id=content_id,
                    window=ToolContentWindow(text=content_id),
                )
                for content_id in request.content_ids
            )
        )


class _RankedExpandBatchReader:
    def __init__(self) -> None:
        self.batches: list[tuple[str, ...]] = []

    async def read_ranked_expand(
        self,
        *,
        request: object,
        session_id: str,
    ) -> ToolContentRankedExpandReadResult:
        content_ids = request.content_ids
        self.batches.append(content_ids)
        return ToolContentRankedExpandReadResult(
            ranked=tuple(
                ToolContentRankedExpandItem(
                    content_id=content_id,
                    rank=index + 1,
                    score=1.0 / (index + 1),
                    window=ToolContentWindow(text=content_id),
                )
                for index, content_id in enumerate(content_ids)
            )
        )


def _markdown_content() -> StoredToolContent:
    text = "The _BRCA_1 marker appears."
    return StoredToolContent(
        content_id="cnt_markdown",
        session_id="session-1",
        content_type="text/markdown",
        text=text,
        metadata={"source_url": "https://example.com/markdown"},
        chunks=(
            ToolContentChunk(
                chunk_index=0,
                start_offset=0,
                end_offset=len(text),
                source_spans=(SourceSpan(0, len(text)),),
                block_kinds=("paragraph",),
            ),
        ),
    )


def _reader(
    stored: StoredToolContent | None = None,
    *,
    max_window_chars: int | None = None,
) -> ToolContentReader:
    return ToolContentReader(
        max_window_chars=max_window_chars,
        ranking_pipeline=RankingPipeline(),
        store=_StoreStub(stored),
    )


@pytest.mark.asyncio
async def test_regex_reader_does_not_repair_markdown_before_matching() -> None:
    result = await _reader(_markdown_content()).read_regex(
        request=ToolContentRegexReadRequest(
            content_ids=("cnt_markdown",),
            pattern="BRCA1",
        ),
        session_id="session-1",
    )

    assert result.matches == ()


@pytest.mark.asyncio
async def test_regex_reader_accepts_broader_pattern_for_malformed_markdown() -> None:
    result = await _reader(_markdown_content()).read_regex(
        request=ToolContentRegexReadRequest(
            content_ids=("cnt_markdown",),
            pattern=r"BRCA.*?1",
        ),
        session_id="session-1",
    )

    assert result.matches[0].window.center_chunk == 0


@pytest.mark.asyncio
async def test_regex_reader_returns_each_match_in_a_chunk() -> None:
    text = "target appears once; target appears twice."
    stored = StoredToolContent(
        content_id="cnt_multiple_matches",
        session_id="session-1",
        content_type="text/plain",
        text=text,
        chunks=(
            ToolContentChunk(
                chunk_index=0,
                start_offset=0,
                end_offset=len(text),
                source_spans=(SourceSpan(0, len(text)),),
            ),
        ),
    )

    result = await _reader(stored).read_regex(
        request=ToolContentRegexReadRequest(
            content_ids=(stored.content_id,),
            pattern="target",
        ),
        session_id=stored.session_id,
    )

    assert len(result.matches) == 2
    assert all(match.window.center_chunk == 0 for match in result.matches)


@pytest.mark.asyncio
async def test_regex_reader_loads_content_ids_concurrently() -> None:
    stored = tuple(
        StoredToolContent(
            content_id=f"cnt_{index}",
            session_id="session-1",
            content_type="text/plain",
            text="target",
            chunks=(
                ToolContentChunk(
                    chunk_index=0,
                    start_offset=0,
                    end_offset=6,
                    source_spans=(SourceSpan(0, 6),),
                ),
            ),
        )
        for index in range(2)
    )
    store = _ConcurrentStoreStub(stored)
    reader = ToolContentReader(
        max_window_chars=None,
        ranking_pipeline=RankingPipeline(),
        store=store,
    )

    result = await reader.read_regex(
        request=ToolContentRegexReadRequest(
            content_ids=tuple(item.content_id for item in stored),
            pattern="target",
        ),
        session_id="session-1",
    )

    assert store.max_active == 2
    assert tuple(match.content_id for match in result.matches) == (
        "cnt_0",
        "cnt_1",
    )


@pytest.mark.asyncio
async def test_regex_reader_matches_markdown_split_identifier_parts() -> None:
    text = "\n\n".join(("_d_ model = 512", "dmodel = 1024", "_d_model_ = 2048"))
    stored = StoredToolContent(
        content_id="cnt_identifiers",
        session_id="session-1",
        content_type="text/markdown",
        text=text,
        chunks=(
            ToolContentChunk(
                chunk_index=0,
                start_offset=0,
                end_offset=15,
                source_spans=(SourceSpan(0, 15),),
            ),
            ToolContentChunk(
                chunk_index=1,
                start_offset=17,
                end_offset=30,
                source_spans=(SourceSpan(17, 30),),
            ),
            ToolContentChunk(
                chunk_index=2,
                start_offset=32,
                end_offset=len(text),
                source_spans=(SourceSpan(32, len(text)),),
            ),
        ),
    )

    result = await _reader(stored).read_regex(
        request=ToolContentRegexReadRequest(
            content_ids=("cnt_identifiers",),
            pattern=r"d_model_?\s*=\s*\d+",
        ),
        session_id="session-1",
    )

    assert tuple(match.window.center_chunk for match in result.matches) == (2,)


@pytest.mark.asyncio
async def test_regex_reader_does_not_relax_literal_underscore_to_whitespace() -> None:
    text = "alpha beta"
    stored = StoredToolContent(
        content_id="cnt_plain",
        session_id="session-1",
        content_type="text/markdown",
        text=text,
        chunks=(
            ToolContentChunk(
                chunk_index=0,
                start_offset=0,
                end_offset=len(text),
                source_spans=(SourceSpan(0, len(text)),),
            ),
        ),
    )

    result = await _reader(stored).read_regex(
        request=ToolContentRegexReadRequest(
            content_ids=("cnt_plain",),
            pattern="alpha_beta",
        ),
        session_id="session-1",
    )

    assert result.matches == ()


@pytest.mark.asyncio
async def test_regex_reader_preserves_literal_identifier_underscores() -> None:
    text = "foo_bar"
    stored = StoredToolContent(
        content_id="cnt_identifier",
        session_id="session-1",
        content_type="text/markdown",
        text=text,
        chunks=(
            ToolContentChunk(
                chunk_index=0,
                start_offset=0,
                end_offset=len(text),
                source_spans=(SourceSpan(0, len(text)),),
            ),
        ),
    )

    result = await _reader(stored).read_regex(
        request=ToolContentRegexReadRequest(
            content_ids=("cnt_identifier",),
            pattern="foobar",
        ),
        session_id="session-1",
    )

    assert result.matches == ()


@pytest.mark.asyncio
async def test_regex_tool_reads_33_content_ids_in_16_item_batches() -> None:
    reader = _RegexBatchReader()
    tool = ToolContentRegexReadTool(reader=reader)
    content_ids = [f"cnt_{index}" for index in range(33)]

    result = await tool.execute(
        {"session_id": "session-1"},
        content_ids=content_ids,
        pattern="target",
        max_matches=64,
    )

    assert [len(batch) for batch in reader.batches] == [16, 16, 1]
    assert tuple(match.content_id for match in result.matches) == tuple(
        [*content_ids[:16], content_ids[-1]]
    )
    assert result.failed == tuple(
        ToolContentReadFailure(content_id=content_id, reason="RuntimeError")
        for content_id in content_ids[16:32]
    )


def test_regex_tool_describes_adaptive_max_matches() -> None:
    reader = _RegexBatchReader()
    tool = ToolContentRegexReadTool(reader=reader)
    llm_spec = tool.definition.llm_spec
    max_matches_schema = llm_spec.parameters_schema.raw["properties"]["max_matches"]

    assert max_matches_schema["default"] == 10
    assert "chunk_count" in max_matches_schema["description"]
    assert "chunk_count" in llm_spec.description


def test_ranked_expand_tool_describes_adaptive_top_k() -> None:
    reader = _RankedExpandBatchReader()
    tool = ToolContentRankedExpandReadTool(reader=reader)
    llm_spec = tool.definition.llm_spec
    top_k_schema = llm_spec.parameters_schema.raw["properties"]["top_k"]

    assert top_k_schema["default"] == 10
    assert "chunk_count" in top_k_schema["description"]
    assert "chunk_count" in llm_spec.description


@pytest.mark.asyncio
async def test_ranked_expand_tool_uses_ranked_result_and_internal_batches() -> None:
    reader = _RankedExpandBatchReader()
    tool = ToolContentRankedExpandReadTool(reader=reader)
    assert tool.definition.llm_spec.name == "tool_content_ranked_expand_read"
    content_ids = [f"cnt_{index}" for index in range(17)]

    result = await tool.execute(
        {"session_id": "session-1"},
        content_ids=content_ids,
        query="relevant evidence",
        top_k=17,
    )

    assert [len(batch) for batch in reader.batches] == [16, 1]
    assert len(result.ranked) == 17
    assert result.ranked[0].rank == 1
    assert result.ranked[0].score == 1.0
    assert not hasattr(result, "matches")


@pytest.mark.asyncio
async def test_regex_tool_applies_global_max_matches_across_batches() -> None:
    reader = _RegexBatchReader()
    tool = ToolContentRegexReadTool(reader=reader)

    result = await tool.execute(
        {"session_id": "session-1"},
        content_ids=[f"cnt_{index}" for index in range(33)],
        pattern="target",
        max_matches=5,
    )

    assert len(result.matches) == 5
    assert [len(batch) for batch in reader.batches] == [16]


@pytest.mark.asyncio
async def test_ranked_expand_tool_globally_orders_and_renumbers_batches() -> None:
    reader = _RankedExpandBatchReader()
    tool = ToolContentRankedExpandReadTool(reader=reader)

    result = await tool.execute(
        {"session_id": "session-1"},
        content_ids=[f"cnt_{index}" for index in range(17)],
        query="relevant evidence",
        top_k=3,
    )

    assert tuple(item.content_id for item in result.ranked) == (
        "cnt_0",
        "cnt_16",
        "cnt_1",
    )
    assert tuple(item.rank for item in result.ranked) == (1, 2, 3)


@pytest.mark.asyncio
async def test_regex_tool_uses_regex_engine_syntax_for_validation() -> None:
    reader = _RegexBatchReader()
    tool = ToolContentRegexReadTool(reader=reader)

    await tool.execute(
        {"session_id": "session-1"},
        content_ids=["cnt_0"],
        pattern=r"(?<=a+)b",
    )

    assert reader.batches == [("cnt_0",)]


@pytest.mark.asyncio
async def test_regex_reader_converts_engine_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TimedOutPattern:
        def finditer(self, text: str, *, timeout: float) -> None:
            raise TimeoutError

    monkeypatch.setattr(
        "chat.application.tools.session_tools.tool_content_read.services.reader.regex.compile",
        lambda pattern: _TimedOutPattern(),
    )

    with pytest.raises(ToolContentRegexTimeoutError):
        await _reader(_markdown_content()).read_regex(
            request=ToolContentRegexReadRequest(
                content_ids=("cnt_markdown",),
                pattern="target",
            ),
            session_id="session-1",
        )


@pytest.mark.asyncio
async def test_range_reader_clamps_start_to_text_end() -> None:
    stored = _markdown_content()

    result = await _reader(stored).read_range(
        content_id=stored.content_id,
        session_id=stored.session_id,
        start=len(stored.text) + 100,
        end=None,
    )

    assert result.window is not None
    assert result.window.text == ""
    assert result.window.start_offset == len(stored.text)
    assert result.window.end_offset == len(stored.text)


@pytest.mark.asyncio
async def test_range_reader_supports_head_and_tail_ranges() -> None:
    stored = _markdown_content()
    reader = _reader(stored)

    head = await reader.read_range(
        content_id=stored.content_id,
        session_id=stored.session_id,
        start=0,
        end=4,
    )
    tail = await reader.read_range(
        content_id=stored.content_id,
        session_id=stored.session_id,
        start=-8,
        end=None,
    )

    assert head.window is not None
    assert head.window.text == "The "
    assert (head.window.start_offset, head.window.end_offset) == (0, 4)
    assert head.window.metadata == {
        "source_url": "https://example.com/markdown"
    }
    assert tail.window is not None
    assert tail.window.text == "appears."
    assert tail.window.end_offset == len(stored.text)


@pytest.mark.asyncio
async def test_range_tool_defaults_to_head_and_caps_requested_range() -> None:
    stored = _markdown_content()
    tool = ToolContentReadTool(reader=_reader(stored, max_window_chars=5))

    result = await tool.execute(
        {"session_id": stored.session_id},
        content_id=stored.content_id,
    )

    assert tool.definition.llm_spec.name == "tool_content_read"
    assert result.window is not None
    assert result.window.text == stored.text[:5]
    assert (result.window.start_offset, result.window.end_offset) == (0, 5)


def test_page_selector_requires_exact_page_label() -> None:
    stored = StoredToolContent(
        content_id="cnt_pages",
        session_id="session-1",
        content_type="text/markdown",
        text="",
        chunks=(
            ToolContentChunk(chunk_index=3, page_labels=("4",)),
            ToolContentChunk(chunk_index=13, page_labels=("14",)),
        ),
        index=ToolContentIndex(
            entries=(
                ToolContentIndexEntry(
                    locator_name="page:4",
                    locator_kind="page",
                    chunk_indices=(3,),
                    page_label="4",
                ),
                ToolContentIndexEntry(
                    locator_name="page:14",
                    locator_kind="page",
                    chunk_indices=(13,),
                    page_label="14",
                ),
            )
        ),
    )

    selected = select_chunks(stored, ToolContentSelector(page_labels=("4",)))

    assert tuple(chunk.chunk_index for chunk in selected) == (3,)


def test_selector_builds_from_tool_payload() -> None:
    selector = ToolContentSelector.from_payload(
        {
            "block_kinds": ["paragraph"],
            "sections": ["Methods"],
            "page_labels": ["4"],
            "anchor_labels": ["Table 1"],
            "chunk_indices": [3],
        }
    )

    assert selector == ToolContentSelector(
        block_kinds=("paragraph",),
        sections=("Methods",),
        page_labels=("4",),
        anchor_labels=("Table 1",),
        chunk_indices=(3,),
    )


def test_window_builder_aggregates_locator_without_redundant_title() -> None:
    text = "first\n\nsecond"
    stored = StoredToolContent(
        content_id="cnt_window",
        session_id="session-1",
        content_type="text/markdown",
        text=text,
        metadata={"source_url": "https://example.com"},
        chunks=(
            ToolContentChunk(
                chunk_index=0,
                start_offset=0,
                end_offset=5,
                source_spans=(SourceSpan(0, 5),),
                section_paths=(("Parent", "Child"),),
                page_labels=("7",),
                anchor_labels=("Table 1",),
            ),
            ToolContentChunk(
                chunk_index=1,
                start_offset=7,
                end_offset=len(text),
                source_spans=(SourceSpan(7, len(text)),),
                section_paths=(("Parent", "Child"),),
                page_labels=("7",),
                anchor_labels=("Table 1", "Figure 2"),
            ),
        ),
    )

    window = ToolContentWindowBuilder().build_expanded_window(
        stored,
        chunks=stored.chunks,
        center_chunk=0,
        merge_before=0,
        merge_after=1,
    )

    assert window.text == text
    assert window.section_paths == (("Parent", "Child"),)
    assert window.page_labels == ("7",)
    assert window.anchor_labels == ("Table 1", "Figure 2")
    assert window.metadata == {"source_url": "https://example.com"}
