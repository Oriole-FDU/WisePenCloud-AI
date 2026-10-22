import json

import pytest
from common.utils.markdown import MarkdownChunk, SourceSpan

from chat.application.tools.core.execution.executor import ToolExecutor
from chat.application.tools.core.execution.hooks.builtin import JsonSchemaCheck
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.output_cache.cache_store import StoredToolContent
from chat.application.tools.session_tools.tool_output import (
    SearchToolOutputBM25Tool,
    search_by_bm25,
)


def _chunk(
    chunk_index: int,
    text: str,
    spans: list[SourceSpan],
    *,
    section_id: str | None = None,
) -> MarkdownChunk:
    return MarkdownChunk(
        chunk_id=f"chunk-{chunk_index}",
        text=text,
        chunk_index=chunk_index,
        source_spans=spans,
        section_id=section_id,
        section_path=["Heading"] if section_id else [],
    )


def _stored(content_id: str, chunks: list[MarkdownChunk]) -> StoredToolContent:
    return StoredToolContent(
        content_id=content_id,
        session_id="session-1",
        text="source",
        chunks=chunks,
    )


@pytest.mark.asyncio
async def test_bm25_searches_multiple_contents_and_merges_chunk_spans() -> None:
    first = _stored(
        "content-1",
        [
            _chunk(0, "ordinary text", [SourceSpan(0, 13)]),
            _chunk(
                1,
                "needle result",
                [SourceSpan(20, 26), SourceSpan(30, 43)],
                section_id="section-1",
            ),
        ],
    )
    second = _stored(
        "content-2",
        [_chunk(0, "another needle result", [SourceSpan(4, 25)])],
    )

    result = await search_by_bm25._search_chunks(
        stored_items=[first, second],
        query="needle",
        top_k=2,
    )

    assert [item.content_id for item in result] == ["content-1", "content-2"]
    assert result[0].start_offset == 20
    assert result[0].end_offset == 43
    assert result[0].section_id == "section-1"
    assert result[0].section_path == "Heading"

    serialized = json.loads(ToolExecutor._coerce_tool_output(result).content)
    assert serialized[0]["content_id"] == "content-1"


@pytest.mark.asyncio
async def test_bm25_tool_deduplicates_ids_and_ignores_unavailable_content(monkeypatch) -> None:
    stored = _stored(
        "content-1",
        [_chunk(0, "needle result", [SourceSpan(0, 13)])],
    )

    async def get_content(*, content_id: str, session_id: str):
        assert session_id == "session-1"
        return stored if content_id == "content-1" else None

    monkeypatch.setattr(search_by_bm25, "get_tool_content", get_content)
    result = await SearchToolOutputBM25Tool().execute(
        {"session_id": "session-1"},
        content_ids=["content-1", "content-1", "missing"],
        query="needle",
        top_k=5,
    )

    assert len(result) == 1
    assert result[0].content_id == "content-1"


@pytest.mark.asyncio
async def test_bm25_schema_injects_default_and_validates_top_k() -> None:
    tool = SearchToolOutputBM25Tool()
    invocation = ToolInvocation(
        tool_call_id="call-1",
        tool_name="search_tool_output_bm25",
        tool_call_arguments={
            "content_ids": ["content-1"],
            "query": "needle",
        },
    )

    valid = await JsonSchemaCheck().check(
        invocation,
        tool.definition.policy,
        tool.definition.llm_spec.parameters_schema,
        {},
    )

    assert valid.ok is True
    assert invocation.tool_call_arguments["top_k"] == 5

    invocation.tool_call_arguments["top_k"] = 21
    invalid = await JsonSchemaCheck().check(
        invocation,
        tool.definition.policy,
        tool.definition.llm_spec.parameters_schema,
        {},
    )

    assert invalid.ok is False
