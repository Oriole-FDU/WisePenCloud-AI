from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from common.utils.document import Section, SourceSpan
from common.utils.ranking import RankCandidate, RankingPipeline, RankQuery, RankRequest
from common.utils.ranking.fusion import WeightedRrfFusion
from common.utils.ranking.rank_gates import (
    HighLowRelevanceGate,
    HighLowRelevanceGateConfig,
)
from common.utils.ranking.rerankers import (
    ZeroEntropyReranker,
    ZeroEntropyRerankerConfig,
)
from common.utils.ranking.scorers import (
    BM25Scorer,
    FieldedBM25Scorer,
    FieldedBM25ScorerConfig,
)
from common.utils.ranking.tokenizer import ThuLacRankingTokenizer
from pydantic import TypeAdapter
from zeroentropy import AsyncZeroEntropy

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolSelectionMode,
    ToolUISpec,
)
from chat.application.tools.core.output_cache.cache_store import (
    StoredToolContent as StoredCachedToolOutput,
)
from chat.application.tools.core.output_cache.cache_store import (
    ToolContentChunk as CachedToolOutputChunk,
)
from chat.application.tools.core.output_cache.cache_store import get_tool_content
from chat.application.tools.session_tools.cached_tool_output_tools.window import (
    CachedToolOutputWindow,
)
from chat.core.config.app_settings import settings

_TIMEOUT_SECONDS = 300.0
_CANDIDATE_LIMIT = 80   # 保留前80个粗召回
_DEFAULT_TOP_K = 5
_MAX_TOP_K = 10
_PARENT_WINDOW_MAX_CHARS = 4_000
_CHUNK_CONTEXT_CHARS = 800
_HIGH_COVERAGE_RATIO = 0.5
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 16,
            "description": "One or more cached tool output content_id values returned in previous tool results.",
        },

        "semantic_query": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Information need used by the semantic reranker. Prefer a complete natural-language "
                "question, such as 'Why is self-attention more efficient than recurrent layers?', but "
                "ordinary semantic statements are also supported. Use formal phrasing and domain-specific "
                "terminology; avoid colloquialisms, slang, and emojis."
            ),
        },
        "lexical_query": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Sparse keywords used by BM25 lexical retrieval. Prefer concise keywords rather than a "
                "full sentence, and try useful synonyms, terminology variants, and cross-language terms "
                "when they may improve matching."
            ),
        },
        "top_k": {
            "type": "integer",
            "default": _DEFAULT_TOP_K,
            "minimum": 1,
            "maximum": _MAX_TOP_K,
            "description": "Maximum final parent contexts and read recommendations returned; at most 10.",
        },
    },
    "required": ["content_ids", "semantic_query", "lexical_query"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class CachedToolOutputSearchBySemanticsItem:
    """语义检索构造出的局部连续父块。"""

    content_id: str
    rank: int  # 所有父块和读取建议合并后的排名，从 1 开始。
    score: float  # 组成该父块的命中子块中的最高相关性分数。
    # section_id/section_path 只用于让模型把父块与确定性章节读取工具关联起来。
    section_id: str | None
    section_path: str | None  # 无真实 Section 时为空。
    window: CachedToolOutputWindow


@dataclass(slots=True)
class CachedToolOutputSectionReadRecommendation:
    """整段 Section 已高度相关，建议读取完整直属正文。"""

    content_id: str
    section_id: str  # 后续交给 read_cached_tool_output_by_section 精确读取。
    title: str
    section_path: str
    rank: int
    score: float
    coverage_ratio: float  # 通过 gate 的 chunk 在该 Section 直属正文中的去重覆盖率。


@dataclass(slots=True)
class CachedToolOutputRangeReadRecommendation:
    """连续局部命中范围过大，建议由 range 工具按原文偏移读取。"""

    content_id: str
    section_id: str | None
    section_path: str | None
    rank: int
    score: float
    # 原文 Python 字符半开区间；模型可把它拆成 start/end 传给 range 工具。
    range: str  # 格式为 "{start_offset} - {end_offset}"。
    matched_chunk_count: int


@dataclass(slots=True)
class CachedToolOutputSearchBySemanticsResult:
    """语义检索结果；读取建议优先于局部父块展示给模型。"""

    # 这些列表使用默认空值，执行出口会通过 TypeAdapter 将空列表移除。
    section_recommendations: list[CachedToolOutputSectionReadRecommendation] = field(
        default_factory=list
    )
    range_recommendations: list[CachedToolOutputRangeReadRecommendation] = field(
        default_factory=list
    )
    results: list[CachedToolOutputSearchBySemanticsItem] = field(
        default_factory=list
    )


_RESULT_ADAPTER = TypeAdapter(CachedToolOutputSearchBySemanticsResult)


@dataclass(frozen=True, slots=True)
class _RankedMatchedChunk:
    """通过排序门控的 chunk 及其构造父块所需定位信息。"""

    stored: StoredCachedToolOutput
    chunk: CachedToolOutputChunk
    section: Section | None
    section_path: str | None
    rank: int
    score: float


@dataclass(frozen=True, slots=True)
class _ParentCandidate:
    """尚未分配最终父块排名的内部候选。"""

    content_id: str
    section: Section | None
    section_path: str | None
    sort_rank: int
    score: float
    window: CachedToolOutputWindow | None = None
    coverage_ratio: float | None = None
    source_range: SourceSpan | None = None
    matched_chunk_count: int = 0


@lru_cache(maxsize=1)
def build_cached_tool_output_search_by_semantics_pipeline() -> RankingPipeline:
    tokenizer = ThuLacRankingTokenizer()
    return RankingPipeline(
        scorers=(
            BM25Scorer(tokenizer=tokenizer),
            FieldedBM25Scorer(
                tokenizer=tokenizer,
                config=FieldedBM25ScorerConfig(
                    field_weights={
                        "section": 2.0,
                        "anchor": 1.5
                    },
                ),
            ),
        ),
        fusion=WeightedRrfFusion(),
        reranker=ZeroEntropyReranker(
            client=AsyncZeroEntropy(
                api_key=settings.ZERO_ENTROPY_API_KEY
            ),
            config=ZeroEntropyRerankerConfig(
                model=settings.RERANKER_MODEL,
            ),
        ),
        gate=HighLowRelevanceGate(
            HighLowRelevanceGateConfig(),
        ),
    )


class CachedToolOutputSearchBySemanticsTool:

    def __init__(
        self,
    ) -> None:
        self._ranking_pipeline = build_cached_tool_output_search_by_semantics_pipeline()
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="search_cached_tool_output_by_semantics",
                description=(
                    "Search semantic chunks from one or more cached tool outputs and return the most "
                    "relevant source windows. Chunks follow Markdown section semantics rather than "
                    "physical page boundaries. top_k counts final parent contexts and read recommendations, "
                    "not child chunks. Short sections are returned in full. When matching chunks cover at "
                    "least half of a long section, section_recommendations lists its exact id for "
                    "read_cached_tool_output_by_section. Local overlapping chunk contexts are merged into "
                    "continuous parent windows bounded to 4000 characters; oversized merged ranges appear "
                    "in range_recommendations for read_cached_tool_output_by_range.\n\n"
                    "Provide semantic_query and lexical_query separately: semantic_query is for the reranker "
                    "and may be a question or ordinary semantic statement, while lexical_query is for BM25 "
                    "and should contain sparse keywords with useful synonym or cross-language variants.\n\n"
                    "Use search_cached_tool_output_by_regex for exact patterns. Use read tools only after "
                    "you know the desired range, pages, or sections."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=_policy(),
            ui_spec=ToolUISpec(
                display_name="语义搜索缓存的工具输出",
                description="按问题语义检索缓存工具输出中的相关片段，并返回可继续按页、章节或范围读取的上下文。",
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del config
        semantic_query = kwargs["semantic_query"].strip()
        lexical_query = kwargs["lexical_query"].strip()
        if not semantic_query or not lexical_query:
            raise ToolExecutionError(
                reason="missing_search_queries",
                detail_reason="semantic_query and lexical_query must be non-empty.",
            )
        try:
            # 允许多个 content 混排；不存在的 content 不参与候选构建。
            stored_items = []
            session_id = context["session_id"]
            for content_id in kwargs["content_ids"]:
                stored = await get_tool_content(
                    content_id=content_id,
                    session_id=session_id,
                )
                if stored is not None:
                    stored_items.append(stored)
            result = await _search_by_semantics(
                stored_items=stored_items,
                semantic_query=semantic_query,
                lexical_query=lexical_query,
                top_k=kwargs["top_k"],
                ranking_pipeline=self._ranking_pipeline,
            )
            # 内部保留 dataclass 的清晰类型；只在模型边界投影为紧凑 dict，省略空列表、None
            # 和默认值，避免把内部结果模型的完整形状暴露给模型。
            return _RESULT_ADAPTER.dump_python(
                result,
                exclude_none=True,
                exclude_defaults=True,
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="search_cached_tool_output_by_semantics_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc


async def _search_by_semantics(
    *,
    stored_items: Sequence[StoredCachedToolOutput],
    semantic_query: str,
    lexical_query: str,
    top_k: int,
    ranking_pipeline: RankingPipeline,
) -> CachedToolOutputSearchBySemanticsResult:
    candidates: list[RankCandidate] = []
    sources: dict[
        str,
        tuple[
            StoredCachedToolOutput,
            CachedToolOutputChunk,
            str | None,
            str | None,
        ],
    ] = {}
    for stored in stored_items:
        sections_by_id = {section.section_id: section for section in stored.sections}
        for chunk in stored.chunks:
            # 排序候选使用缓存时保存的 chunk 正文；locator 只负责后续回源和父块组装，
            # 不把结构元数据误当成语义正文。
            text = chunk.text
            if not text:
                continue
            content_id = stored.content_id
            candidate_id = f"{content_id}:chunk:{chunk.chunk_index}"
            section = sections_by_id.get(chunk.section_id)
            section_id = section.section_id if section else None
            section_path = " > ".join(section.section_path) if section else None
            # 先保存候选到原文 chunk 的映射。排序完成后只处理通过 gate 的候选，
            # 这样低相关 chunk 不会消耗父块预算或 top_k 名额。
            sources[candidate_id] = (stored, chunk, section_id, section_path)
            candidates.append(
                RankCandidate(
                    candidate_id=candidate_id,
                    text=(
                        f"{section_path}\n{text}"
                        if section_path
                        else text
                    ),
                    fields={
                        "section": section_path or "",
                        "anchor": "\n".join(chunk.anchor_labels),
                    },
                    group_key=content_id,
                )
            )

    if not candidates:
        # 没有候选时，返回空检索结果。
        return CachedToolOutputSearchBySemanticsResult()

    # RankingPipeline 的请求模型要求 candidates 为 tuple。先保留通过 gate 的全部子块，
    # 再由本工具按父块组装并应用 top_k，避免一个 Section 的多个小子块挤占最终结果。
    result = await ranking_pipeline.arank(
        RankRequest(
            query=RankQuery(
                semantic_query=semantic_query,
                lexical_query=lexical_query,
            ),
            candidates=tuple(candidates),
            top_k=min(len(candidates), _CANDIDATE_LIMIT),
            candidate_limit=min(len(candidates), _CANDIDATE_LIMIT),
        )
    )

    # 排序阶段以 chunk 为单位，组装阶段才恢复父块语义；同一 Section 的多个命中
    # 必须先聚合，否则每个小 chunk 都会错误占用一个最终 top_k。
    matched_chunks_by_parent: dict[
        tuple[str, str | None],
        list[_RankedMatchedChunk],
    ] = {}
    for item in result.ranked:
        source = sources.get(item.candidate_id)
        if source is None:
            continue
        stored, chunk, section_id, section_path = source
        section = next(
            (
                section
                for section in stored.sections
                if section.section_id == section_id
            ),
            None,
        )
        parent_key = (stored.content_id, section_id)
        matched_chunks_by_parent.setdefault(parent_key, []).append(
            _RankedMatchedChunk(
                stored=stored,
                chunk=chunk,
                section=section,
                section_path=section_path,
                rank=item.rank,
                score=item.score,
            )
        )

    parent_candidates = [
        parent_candidate
        for matched_chunks in matched_chunks_by_parent.values()
        for parent_candidate in _build_parent_candidates(matched_chunks)
    ]
    return _build_search_result(parent_candidates, top_k=top_k)


def _build_parent_candidates(
    matched_chunks: Sequence[_RankedMatchedChunk],
) -> list[_ParentCandidate]:
    """按 Section 的长度和命中密度，将通过 gate 的 chunk 组装为父块候选。

    长度判断必须先于覆盖率判断：短 Section 可以完整返回，不需要把一次可读内容
    拆成局部窗口；只有长 Section 才需要在“读整章”和“局部父块”之间分流。
    """

    first_match = min(matched_chunks, key=lambda item: item.rank)
    section = first_match.section
    stored = first_match.stored
    section_path = first_match.section_path

    if section is not None:
        scope = section.own_span
        if _span_length(scope) <= _PARENT_WINDOW_MAX_CHARS:
            # 短 Section 直接完整返回，避免为本可一次理解的内容引入窗口组装。
            return [
                _ParentCandidate(
                    content_id=stored.content_id,
                    section=section,
                    section_path=section_path,
                    sort_rank=first_match.rank,
                    score=max(item.score for item in matched_chunks),
                    window=_window_from_span(
                        stored=stored,
                        span=scope,
                        scope=scope,
                    ),
                    matched_chunk_count=len(matched_chunks),
                )
            ]

        # 以原文区间并集计算覆盖率，避免 chunk overlap 或同一 chunk 的多个 source span
        # 重复放大“整章高度相关”的判断。
        coverage_ratio = _covered_length(
            spans=[item.chunk.source_spans for item in matched_chunks],
            scope=scope,
        ) / _span_length(scope)
        if coverage_ratio >= _HIGH_COVERAGE_RATIO:
            return [
                _ParentCandidate(
                    content_id=stored.content_id,
                    section=section,
                    section_path=section_path,
                    sort_rank=first_match.rank,
                    score=max(item.score for item in matched_chunks),
                    coverage_ratio=coverage_ratio,
                    matched_chunk_count=len(matched_chunks),
                )
            ]
    else:
        # 无标题文本没有 Section 身份，但仍有完整正文范围；复用同一组装逻辑，
        # 避免把一个检索 chunk 机械地当成它自己的父块。
        scope = SourceSpan(0, len(stored.text))

    return _build_local_parent_candidates(
        stored=stored,
        section=section,
        section_path=section_path,
        scope=scope,
        matched_chunks=matched_chunks,
    )


def _build_local_parent_candidates(
    *,
    stored: StoredCachedToolOutput,
    section: Section | None,
    section_path: str | None,
    scope: SourceSpan,
    matched_chunks: Sequence[_RankedMatchedChunk],
) -> list[_ParentCandidate]:
    """将重叠的 chunk 上下文贪婪合并为局部父块或大范围读取建议。

    扩展窗口按原文位置排序；相交窗口形成同一个父块，且合并后的父块继续参与后续
    窗口判断，因此 A 与 B、B 与 C 相交时，三者会形成一个连续候选。
    """

    expanded_chunks = sorted(
        (
            (
                SourceSpan(
                    # 扩展范围同时受 Section/全文边界约束，不能把邻接标题或其他内容
                    # 带入当前父块。
                    max(item.chunk.start_offset - _CHUNK_CONTEXT_CHARS, scope.start_offset),
                    min(item.chunk.end_offset + _CHUNK_CONTEXT_CHARS, scope.end_offset),
                ),
                item,
            )
            for item in matched_chunks
        ),
        key=lambda value: (value[0].start_offset, value[0].end_offset),
    )
    parent_candidates: list[_ParentCandidate] = []
    current_span, first_item = expanded_chunks[0]
    current_items = [first_item]

    for expanded_span, item in expanded_chunks[1:]:
        if expanded_span.start_offset <= current_span.end_offset:
            # 使用区间相交而非 rank 相邻判断；rank 顺序只决定候选优先级，不能决定正文连续性。
            current_span = SourceSpan(
                current_span.start_offset,
                max(current_span.end_offset, expanded_span.end_offset),
            )
            current_items.append(item)
            continue

        # 新窗口与当前父块断开，先封存当前父块，再从新窗口开始下一组。
        parent_candidates.append(
            _local_parent_candidate(
                stored=stored,
                section=section,
                section_path=section_path,
                scope=scope,
                span=current_span,
                matched_chunks=current_items,
            )
        )
        current_span = expanded_span
        current_items = [item]

    parent_candidates.append(
        _local_parent_candidate(
            stored=stored,
            section=section,
            section_path=section_path,
            scope=scope,
            span=current_span,
            matched_chunks=current_items,
        )
    )
    return parent_candidates


def _local_parent_candidate(
    *,
    stored: StoredCachedToolOutput,
    section: Section | None,
    section_path: str | None,
    scope: SourceSpan,
    span: SourceSpan,
    matched_chunks: Sequence[_RankedMatchedChunk],
) -> _ParentCandidate:
    sort_rank = min(item.rank for item in matched_chunks)
    score = max(item.score for item in matched_chunks)
    if _span_length(span) > _PARENT_WINDOW_MAX_CHARS:
        # 重叠命中覆盖的连续原文超过父块预算时，不能截断任何命中；将连续范围交给
        # range 工具续读，保留完整命中范围而不是返回一个语义不完整的父块。
        return _ParentCandidate(
            content_id=stored.content_id,
            section=section,
            section_path=section_path,
            sort_rank=sort_rank,
            score=score,
            source_range=span,
            matched_chunk_count=len(matched_chunks),
        )
    return _ParentCandidate(
        content_id=stored.content_id,
        section=section,
        section_path=section_path,
        sort_rank=sort_rank,
        score=score,
        window=_window_from_span(stored=stored, span=span, scope=scope),
        matched_chunk_count=len(matched_chunks),
    )


def _build_search_result(
    parent_candidates: Sequence[_ParentCandidate],
    *,
    top_k: int,
) -> CachedToolOutputSearchBySemanticsResult:
    """按最早命中 rank 选出最终父块；每个建议或窗口各占一个 top_k。

    一个内部候选最终只会落入三种公开视图之一：Section 读取建议、range 读取建议或
    普通父块。统一在这里分配 rank，确保不同视图共享同一套 top_k 计数。
    """

    # 候选的 sort_rank 是其内部命中 chunk 的最早 rank；先命中的信息岛优先进入最终结果。
    selected_candidates = sorted(
        parent_candidates,
        key=lambda item: item.sort_rank,
    )[:top_k]
    result = CachedToolOutputSearchBySemanticsResult()
    for rank, candidate in enumerate(selected_candidates, start=1):
        if candidate.coverage_ratio is not None:
            section = candidate.section
            if section is None:
                raise ValueError("section recommendation requires a section")
            result.section_recommendations.append(
                CachedToolOutputSectionReadRecommendation(
                    content_id=candidate.content_id,
                    section_id=section.section_id,
                    title=section.title,
                    section_path=candidate.section_path or "",
                    rank=rank,
                    score=candidate.score,
                    coverage_ratio=candidate.coverage_ratio,
                )
            )
            continue

        if candidate.source_range is not None:
            result.range_recommendations.append(
                CachedToolOutputRangeReadRecommendation(
                    content_id=candidate.content_id,
                    section_id=(
                        candidate.section.section_id
                        if candidate.section is not None
                        else None
                    ),
                    section_path=candidate.section_path,
                    rank=rank,
                    score=candidate.score,
                    range=_format_range(candidate.source_range),
                    matched_chunk_count=candidate.matched_chunk_count,
                )
            )
            continue

        window = candidate.window
        if window is None:
            raise ValueError("parent candidate requires a window or recommendation")
        result.results.append(
            CachedToolOutputSearchBySemanticsItem(
                content_id=candidate.content_id,
                rank=rank,
                score=candidate.score,
                section_id=(
                    candidate.section.section_id
                    if candidate.section is not None
                    else None
                ),
                section_path=candidate.section_path,
                window=window,
            )
        )
    return result


def _covered_length(
    *,
    spans: Sequence[Sequence[SourceSpan]],
    scope: SourceSpan,
) -> int:
    """计算多个可能重叠的 chunk 在指定父范围内的去重覆盖字符数。

    这里使用 Python 字符半开区间；先裁剪到父范围，再排序合并，得到可解释且不重复
    的覆盖长度。
    """

    ranges = sorted(
        (
            SourceSpan(
                max(span.start_offset, scope.start_offset),
                min(span.end_offset, scope.end_offset),
            )
            for chunk_spans in spans
            for span in chunk_spans
            if span.start_offset < scope.end_offset
            and span.end_offset > scope.start_offset
        ),
        key=lambda span: (span.start_offset, span.end_offset),
    )
    if not ranges:
        return 0

    covered_length = 0
    current = ranges[0]
    for span in ranges[1:]:
        if span.start_offset <= current.end_offset:
            current = SourceSpan(
                current.start_offset,
                max(current.end_offset, span.end_offset),
            )
            continue
        covered_length += _span_length(current)
        current = span
    return covered_length + _span_length(current)


def _window_from_span(
    *,
    stored: StoredCachedToolOutput,
    span: SourceSpan,
    scope: SourceSpan,
) -> CachedToolOutputWindow:
    # window 的 offset 仍指向缓存原文，而不是拼装后文本；这样 truncated 时才能从
    # end_offset 继续调用 range 工具读取原文。
    return CachedToolOutputWindow(
        text=stored.text[span.start_offset : span.end_offset],
        start_offset=span.start_offset,
        end_offset=span.end_offset,
        # 局部父块不是完整 Section 或完整 flat 文本时，需要向模型暴露仍有可续读内容。
        truncated=span != scope,
    )


def _span_length(span: SourceSpan) -> int:
    return span.end_offset - span.start_offset


def _format_range(span: SourceSpan) -> str:
    return f"{span.start_offset} - {span.end_offset}"


def _policy() -> ToolPolicy:
    return ToolPolicy(
        expose_by_default=False,
        selection_mode=ToolSelectionMode.CONTEXTUAL,
        persist_output=True,
        risk_level=ToolRiskLevel.LOW,
        required_context_keys=("session_id",),
        timeout_seconds=_TIMEOUT_SECONDS,
    )
