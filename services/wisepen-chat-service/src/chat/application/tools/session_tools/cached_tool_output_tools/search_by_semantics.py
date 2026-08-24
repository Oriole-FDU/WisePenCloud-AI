"""基于会话缓存的轻量级嵌入式 RAG（小型检索增强生成）系统。

本模块为大模型提供在单个或多个缓存工具输出（Cached Tool Output）之上的端到端语义检索与上下文组装能力，
遵循“细粒度索引召回，粗粒度拓扑组装，分级决策呈现”的设计哲学：

1. 混合检索与重排流水线 (Hybrid Retrieval & Reranking):
   - 采用双通道查询输入：自然语言问题 (semantic_query) 与稀疏关键词 (lexical_query)。
   - 融合 BM25 与考虑文档结构权重的 Fielded BM25 (加权 Section 路径与 Anchor 标签)。
   - 使用 Cross-Encoder (ZeroEntropy) 深度语义重排，并通过 HighLowRelevanceGate 动态过滤低相关噪声。

2. 文档拓扑驱动的父块聚合 (Parent Document Aggregation):
   - 以原子 Chunk 为召回单位保证检索灵敏度，召回后按 (content_id, section_id) 聚合。
   - 弃用脆弱的字符几何切分，基于预存的 chunk_index 离散拓扑向同 Section 邻近 Chunk 扩展上下文，
     在保证段落边界完整的同时，天然阻断跨章节的语义渗漏。

3. 智能三路分流呈现策略 (Tri-branch Presentation Strategy):
   根据章节长度、连通块覆盖密度与模型阅读预算，统一在全局 Top-K 下分流输出：
   - 短章节 (≤ 4k chars): 直接返回整章完整视窗。
   - 高密度章节 (≤ 8k chars 且覆盖率 ≥ 80%): 降级为 Section 读取建议，引导模型按章精准精读。
   - 局部连通块: ≤ 6k chars 返回局部连续视窗；超大连通块生成 Range 读取建议，支持按偏移量连续续读。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from common.utils.document import Section, SourceSpan
from common.utils.ranking import RankCandidate, RankingPipeline, RankQuery, RankRequest
from common.utils.ranking.diversifiers import MmrDiversifier
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
_CANDIDATE_LIMIT = 80  # 保留前80个粗召回
_DEFAULT_TOP_K = 5  # 返回topk个父块
_MAX_TOP_K = 10
_SHORT_SECTION_MAX_CHARS = 4_000  # 只控制短 Section 是否直接返回全文。
_LOCAL_PARENT_HARD_MAX_CHARS = 6_000  # 只控制局部父块的文本输出上限。
_SECTION_RECOMMENDATION_MAX_CHARS = 8_000  # 控制是否允许建议整章读取。
_SECTION_RECOMMENDATION_COVERAGE = 0.8  # 高相关性section覆盖率阈值

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
    coverage_ratio: float  # 扩展后单个连通命中块在 Section 中的去重覆盖率。


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

# 由于含有大量空白可选分支，此处进行紧凑序列化处理
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
                # 额外奖励章节命中和明确的锚点命中
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
            HighLowRelevanceGateConfig(),   # rank门控可确保大量命中均具有强相关性
        ),
        diversifiers=(
            MmrDiversifier(tokenizer=tokenizer),
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
                description = (
                    "Perform hybrid semantic search across cached tool outputs to retrieve relevant context windows "
                    "and read recommendations. Merges nearby matches to preserve complete context without fragmenting sections.\n\n"
                    "Parameters:\n"
                    "- semantic_query: Natural language question or descriptive statement for the semantic reranker.\n"
                    "- lexical_query: Concise keywords, synonyms, and terminology variants for BM25 matching.\n\n"
                    "Returns a unified ranking of:\n"
                    "1. results: Ready-to-read local text windows.\n"
                    "2. section_recommendations: High-coverage sections; follow up with read_cached_tool_output_by_section.\n"
                    "3. range_recommendations: Broad continuous matches; follow up with read_cached_tool_output_by_range.\n\n"
                    "For exact literal patterns or identifiers, use search_cached_tool_output_by_regex instead."
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
            Section | None,
            str | None,
        ],
    ] = {}  # 按照(stored, chunk, section, section_path)缓存，便于下游快速读取
    for stored in stored_items:
        sections_by_id = {section.section_id: section for section in stored.sections}
        for chunk in stored.chunks:
            # 排序候选使用缓存时保存的 chunk 正文
            text = chunk.text
            if not text:
                continue
            content_id = stored.content_id
            candidate_id = f"{content_id}:chunk:{chunk.chunk_index}"
            section = sections_by_id.get(chunk.section_id)
            section_path = (
                " > ".join(section.section_path)
                if section and section.section_path
                else None
            )
            # 先保存候选到原文 chunk 的映射。排序完成后只处理通过 gate 的候选，
            # 这样低相关 chunk 不会消耗父块预算或 top_k 名额。
            sources[candidate_id] = (stored, chunk, section, section_path)
            candidates.append(
                    RankCandidate(
                        candidate_id=candidate_id,
                        # section_path已经是过滤后的结果，此处直接信任并插入开头，有利于提高重排的准确性
                        text=(
                            f"{section_path}\n{text}"
                            if section_path
                            else text
                        ),
                    fields={
                        "section": section_path or "",
                        "anchor": "\n".join(chunk.anchor_labels),
                    },
                    # MMR 只按 content_id 惩罚，在不同文档间做多样性平衡，Section 内的高相关命中仍可共同保留。
                    group_key=content_id,
                )
            )

    if not candidates:
        # 没有候选时，返回空检索结果。
        return CachedToolOutputSearchBySemanticsResult()

    # 先保留通过 gate 的全部子块， 再由本工具按父块组装并应用 top_k，避免一个 Section 的多个小子块挤占最终结果。
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

    # 收集一个section内所有命中的子块
    matched_chunks_by_parent: dict[
        tuple[str, str | None],
        list[_RankedMatchedChunk],
    ] = {}
    for item in result.ranked:
        source = sources.get(item.candidate_id)
        if source is None:
            continue
        stored, chunk, section, section_path = source
        parent_key = (
            stored.content_id,
            section.section_id if section else None,
        )
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
    # 构造父块候选
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

    先构造不受局部输出预算限制的完整扩展连通块，再决定是否推荐读取整个 Section。
    只有整章推荐失败后，局部父块才应用 6000 字符硬上限。
    """

    first_match = min(matched_chunks, key=lambda item: item.rank)
    section = first_match.section
    stored = first_match.stored
    section_path = first_match.section_path

    if section is not None:
        scope = section.own_span
        if _span_length(scope) <= _SHORT_SECTION_MAX_CHARS:
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
    else:
        # 无标题文本没有 Section 身份，但仍有完整正文范围；复用同一组装逻辑，
        # 避免把一个检索 chunk 机械地当成它自己的父块。
        scope = SourceSpan(0, len(stored.text))

    expanded_groups = _build_expanded_chunk_groups(
        scope=scope,
        matched_chunks=matched_chunks,
    )
    # 如果section长度低于最大建议章节阈值，连通窗口唯一且连续，覆盖率超过80%，则落入建议章节分区
    if (
        section is not None
        and _span_length(scope) <= _SECTION_RECOMMENDATION_MAX_CHARS
        and len(expanded_groups) == 1
    ):
        expanded_spans = [span for span, _ in expanded_groups]
        coverage_ratio = _covered_length(
            spans=expanded_spans,
            scope=scope,
        ) / _span_length(scope)
        if coverage_ratio >= _SECTION_RECOMMENDATION_COVERAGE:
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

    return _build_local_parent_candidates(
        stored=stored,
        section=section,
        section_path=section_path,
        scope=scope,
        expanded_groups=expanded_groups,
    )


def _build_local_parent_candidates(
    *,
    stored: StoredCachedToolOutput,
    section: Section | None,
    section_path: str | None,
    scope: SourceSpan,
    expanded_groups: Sequence[
        tuple[SourceSpan, list[_RankedMatchedChunk]]
    ],
) -> list[_ParentCandidate]:
    """将重叠的 chunk 上下文贪婪合并为局部父块或大范围读取建议。

    扩展范围已经在 Section 推荐判断前完成合并；本函数只负责按 6000 字符硬上限
    将每个完整连通块投影为文本窗口或单个连续 range。
    """
    return [
        _local_parent_candidate(
            stored=stored,
            section=section,
            section_path=section_path,
            scope=scope,
            span=expanded_span,
            matched_chunks=group_items,
        )
        for expanded_span, group_items in expanded_groups
    ]


def _build_expanded_chunk_groups(
    *,
    scope: SourceSpan,
    matched_chunks: Sequence[_RankedMatchedChunk],
) -> list[tuple[SourceSpan, list[_RankedMatchedChunk]]]:
    """按缓存 chunk 顺序合并命中组，并向两侧各扩展一个同 Section chunk。

    ToolContentChunk 的 ``chunk_index 是缓存时确定的顺序索引，因此不需要重新
    扫描正文或按字符猜测邻居。扩展只接受同一 section_id 的邻居，避免父块跨过
    标题边界；组内最多允许隔着一个同 Section 未命中 chunk，保持原有连续上下文语义。
    """

    sorted_chunks = sorted(
        matched_chunks,
        key=lambda item: (item.chunk.start_offset, item.chunk.end_offset),
    )
    current_items = [sorted_chunks[0]]
    expanded_groups: list[
        tuple[SourceSpan, list[_RankedMatchedChunk]]
    ] = []

    def append_current_group() -> None:
        first_item = current_items[0]
        last_item = current_items[-1]
        section_id = (
            first_item.section.section_id
            if first_item.section is not None
            else None
        )
        previous_chunk = _neighbor_chunk(
            stored=first_item.stored,
            chunk=first_item.chunk,
            section_id=section_id,
            offset=-1,
        )
        next_chunk = _neighbor_chunk(
            stored=last_item.stored,
            chunk=last_item.chunk,
            section_id=section_id,
            offset=1,
        )
        expanded_groups.append(
            (
                SourceSpan(
                    max(
                        (
                            previous_chunk.start_offset
                            if previous_chunk is not None
                            else first_item.chunk.start_offset
                        ),
                        scope.start_offset,
                    ),
                    min(
                        (
                            next_chunk.end_offset
                            if next_chunk is not None
                            else last_item.chunk.end_offset
                        ),
                        scope.end_offset,
                    ),
                ),
                current_items.copy(),
            )
        )

    for item in sorted_chunks[1:]:
        previous_item = current_items[-1]
        if _can_join_chunk_group(previous_item, item):
            current_items.append(item)
            continue

        append_current_group()
        current_items = [item]

    append_current_group()
    return expanded_groups


def _can_join_chunk_group(
    previous_item: _RankedMatchedChunk,
    current_item: _RankedMatchedChunk,
) -> bool:
    """判断两个命中 chunk 是否仍属于同一连续上下文组。"""

    if previous_item.section is not None and current_item.section is not None:
        if previous_item.section.section_id != current_item.section.section_id:
            return False
    elif previous_item.section is not current_item.section:
        return False

    chunk_gap = current_item.chunk.chunk_index - previous_item.chunk.chunk_index
    return 1 <= chunk_gap <= 2


def _neighbor_chunk(
    *,
    stored: StoredCachedToolOutput,
    chunk: CachedToolOutputChunk,
    section_id: str | None,
    offset: int,
) -> CachedToolOutputChunk | None:
    """按 chunk_index 获取同 Section 的相邻缓存 chunk。"""

    neighbor_index = chunk.chunk_index + offset
    if not 0 <= neighbor_index < len(stored.chunks):
        return None
    neighbor = stored.chunks[neighbor_index]
    if neighbor.chunk_index != neighbor_index or neighbor.section_id != section_id:
        return None
    return neighbor


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
    if _span_length(span) > _LOCAL_PARENT_HARD_MAX_CHARS:
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
    # 小窗口直接返回整个窗口
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
    """按父块代表分数选出最终候选；每个建议或窗口各占一个 top_k。

    一个内部候选最终只会落入三种公开视图之一：Section 读取建议、range 读取建议或
    普通父块。统一在这里分配 rank，确保不同视图共享同一套 top_k 计数。
    """

    # 代表分数取父块内最高命中分数；最早 rank 只用于相同分数时稳定排序。
    selected_candidates = sorted(
        parent_candidates,
        key=lambda item: (-item.score, item.sort_rank),
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
    spans: Sequence[SourceSpan],
    scope: SourceSpan,
) -> int:
    """计算多个可能重叠的范围在指定父范围内的去重覆盖字符数。

    这里使用 Python 字符半开区间；先裁剪到父范围，再排序合并，得到可解释且不重复
    的覆盖长度。
    """

    ranges = sorted(
        (
            SourceSpan(
                max(span.start_offset, scope.start_offset),
                min(span.end_offset, scope.end_offset),
            )
            for span in spans
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
