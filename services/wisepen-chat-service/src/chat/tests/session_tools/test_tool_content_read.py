from __future__ import annotations

import pytest

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentChunk,
)
from chat.application.tools.session_tools.tool_content_read import (
    ToolContentRegexReadRequest,
    ToolContentRankedReadRequest,
)
from chat.application.tools.session_tools.tool_content_read.services.reader import (
    ToolContentReader,
)
from chat.application.tools.session_tools.tool_content_read.tools import (
    ToolContentReadByLocatorTool,
    ToolContentReadTool,
    ToolContentRegexReadTool,
    ToolContentRankedReadTool,
)
from common.utils.chunkers import LocatorKind, SourceSpan, TextLocator
from common.utils.ranking import RankResult, RankedCandidate


class _StoreStub:
    def __init__(self, *stored: StoredToolContent) -> None:
        self._stored = {item.content_id: item for item in stored}

    async def get(
        self,
        *,
        content_id: str,
        session_id: str,
    ) -> StoredToolContent | None:
        stored = self._stored.get(content_id)
        if stored is None or stored.session_id != session_id:
            return None
        return stored


class _RankingPipelineStub:
    async def arank(self, request):
        candidate = request.candidates[-1]
        return RankResult(
            ranked=(RankedCandidate(candidate=candidate, rank=1, score=0.9),),
            total_candidates=len(request.candidates),
        )


def _stored_content() -> StoredToolContent:
    text = (
        "<!-- page 1 -->\n\n# 第一节\n\nAlpha begins here.\n\n"
        "<!-- page 2 -->\n\n## 第二节\n\nBeta ends here."
    )
    alpha_start = text.index("# 第一节")
    alpha_end = text.index("<!-- page 2 -->")
    beta_start = text.index("## 第二节")
    return StoredToolContent(
        content_id="cnt_doc",
        session_id="session-1",
        content_type="text/markdown",
        text=text,
        chunks=(
            ToolContentChunk(
                chunk_index=0,
                source_spans=(SourceSpan(alpha_start, alpha_end),),
                section_paths=(("第一节",),),
                page_labels=("1",),
            ),
            ToolContentChunk(
                chunk_index=1,
                source_spans=(SourceSpan(beta_start, len(text)),),
                section_paths=(("第一节", "第二节"),),
                page_labels=("2",),
            ),
        ),
        locators=(
            TextLocator("page:1", LocatorKind.PAGE, 0, alpha_end),
            TextLocator("page:2", LocatorKind.PAGE, alpha_end, len(text)),
            TextLocator(
                "section:第一节",
                LocatorKind.SECTION,
                alpha_start,
                len(text),
            ),
            TextLocator(
                "section:第一节 > 第二节",
                LocatorKind.SECTION,
                beta_start,
                len(text),
            ),
        ),
        metadata={"source_url": "https://example.com/doc"},
    )


def _reader(stored: StoredToolContent | None = None) -> ToolContentReader:
    return ToolContentReader(
        max_window_chars=100_000,
        ranking_pipeline=_RankingPipelineStub(),
        store=_StoreStub(*(stored,) if stored else ()),
    )


@pytest.mark.asyncio
async def test_range_read_slices_authoritative_text() -> None:
    stored = _stored_content()
    result = await _reader(stored).read_range(
        content_id=stored.content_id,
        session_id=stored.session_id,
        start=stored.text.index("Alpha"),
        end=stored.text.index("Alpha") + 5,
    )

    assert result.window is not None
    assert result.window.text == "Alpha"
    assert result.window.start_offset == stored.text.index("Alpha")


@pytest.mark.asyncio
async def test_locator_read_bypasses_chunks_and_returns_complete_section() -> None:
    stored = _stored_content()
    result = await _reader(stored).read_locator(
        content_id=stored.content_id,
        session_id=stored.session_id,
        locator_name="section:第一节",
    )

    assert result.reason is None
    assert len(result.windows) == 1
    assert "Alpha begins here" in result.windows[0].text
    assert "Beta ends here" in result.windows[0].text


@pytest.mark.asyncio
async def test_locator_read_reports_unknown_locator() -> None:
    result = await _reader(_stored_content()).read_locator(
        content_id="cnt_doc",
        session_id="session-1",
        locator_name="page:99",
    )

    assert result.reason == "locator_not_found"
    assert result.windows == ()


@pytest.mark.asyncio
async def test_regex_scans_complete_source_across_chunk_boundaries() -> None:
    text = "prefix CROSS" + "BOUNDARY suffix"
    stored = StoredToolContent(
        content_id="cnt_cross",
        session_id="session-1",
        content_type="text/plain",
        text=text,
        chunks=(
            ToolContentChunk(0, (SourceSpan(0, 12),)),
            ToolContentChunk(1, (SourceSpan(12, len(text)),)),
        ),
    )
    result = await _reader(stored).read_regex(
        request=ToolContentRegexReadRequest(
            content_ids=(stored.content_id,),
            pattern="CROSSBOUNDARY",
            context_chars=3,
        ),
        session_id=stored.session_id,
    )

    assert len(result.matches) == 1
    assert result.matches[0].match_start == text.index("CROSSBOUNDARY")


@pytest.mark.asyncio
async def test_search_ranks_semantic_chunks_and_exposes_locator_names() -> None:
    stored = _stored_content()
    result = await _reader(stored).read_ranked(
        request=ToolContentRankedReadRequest(
            content_ids=(stored.content_id,),
            query="Where does beta end?",
            top_k=1,
        ),
        session_id=stored.session_id,
    )

    assert len(result.ranked) == 1
    assert result.ranked[0].chunk_index == 1
    assert result.ranked[0].window.text.strip().endswith("Beta ends here.")
    assert result.ranked[0].window.locator_names == (
        "section:第一节 > 第二节",
        "page:2",
    )


def test_tools_expose_new_contract_without_selectors() -> None:
    reader = _reader()
    tools = (
        ToolContentReadTool(reader=reader),
        ToolContentReadByLocatorTool(reader=reader),
        ToolContentRegexReadTool(reader=reader),
        ToolContentRankedReadTool(reader=reader),
    )

    assert tuple(tool.definition.llm_spec.name for tool in tools) == (
        "tool_content_read",
        "tool_content_read_by_locator",
        "tool_content_regex_read",
        "tool_content_ranked_read",
    )
    assert all(
        "selector" not in tool.definition.llm_spec.parameters_schema.properties
        for tool in tools
    )
