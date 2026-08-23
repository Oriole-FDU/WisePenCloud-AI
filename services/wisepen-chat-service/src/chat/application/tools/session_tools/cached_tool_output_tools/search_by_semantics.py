from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

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
            "description": "Maximum ranked parent sections returned; at most 10.",
        },
    },
    "required": ["content_ids", "semantic_query", "lexical_query"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class CachedToolOutputSearchBySemanticsItem:
    """语义检索的模型可见父块及其子块相关性。"""

    content_id: str
    rank: int  # 去重后的父块排名，从 1 开始。
    score: float  # 命中该父块的最高子块相关性分数。
    section_id: str | None  # 有标题文本时可直接交给按 section 读取工具。
    section_path: str | None  # 无真实 Section 时为空。
    window: CachedToolOutputWindow


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
                    "physical page boundaries. Small child chunks identify relevant parent sections; "
                    "each result includes content_id, section_id, section_path, rank, score, and "
                    "a parent window bounded to 4000 characters.\n\n"
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
    ) -> list[CachedToolOutputSearchBySemanticsItem]:
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
            return await _search_by_semantics(
                stored_items=stored_items,
                semantic_query=semantic_query,
                lexical_query=lexical_query,
                top_k=kwargs["top_k"],
                ranking_pipeline=self._ranking_pipeline,
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
) -> list[CachedToolOutputSearchBySemanticsItem]:
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
            # 排序候选从 chunk 的原文 span 回读，避免只拿 locator 元数据参与语义检索。
            text = chunk.text
            if not text:
                continue
            content_id = stored.content_id
            candidate_id = f"{content_id}:chunk:{chunk.chunk_index}"
            section = sections_by_id.get(chunk.section_id)
            section_id = section.section_id if section else None
            section_path = " > ".join(section.section_path) if section else None
            # sources 保存候选和原文 chunk 的映射，排序完成后再构造可读窗口。
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
        return []

    # RankingPipeline 的请求模型要求 candidates 为 tuple，这里是外部契约不是内部数组语义。
    # top_k 是父 Section 数量；先让 pipeline 排完候选，再按父块去重，避免同一 Section
    # 的多个小子块占满最终结果。
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

    results: list[CachedToolOutputSearchBySemanticsItem] = []
    seen_parents: set[tuple[str, str]] = set()
    for item in result.ranked:
        source = sources.get(item.candidate_id)
        if source is None:
            continue
        stored, chunk, section_id, section_path = source
        content_id = stored.content_id
        parent_key = (content_id, section_id or item.candidate_id)
        if parent_key in seen_parents:
            continue
        seen_parents.add(parent_key)

        if section_id is None:
            # 无标题文本没有父 Section，子块本身就是唯一可回读的父块。
            window = CachedToolOutputWindow(
                text=chunk.text,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                truncated=False,
            )
        else:
            section = next(
                section
                for section in stored.sections
                if section.section_id == section_id
            )
            start_offset = section.own_span.start_offset
            end_offset = min(
                section.own_span.end_offset,
                start_offset + _PARENT_WINDOW_MAX_CHARS,
            )
            # 父块窗口允许比缓存子块更大，但仍保留原文半开 offset 供后续续读。
            window = CachedToolOutputWindow(
                text=stored.text[start_offset:end_offset],
                start_offset=start_offset,
                end_offset=end_offset,
                truncated=end_offset < section.own_span.end_offset,
            )

        results.append(
            CachedToolOutputSearchBySemanticsItem(
                content_id=content_id,
                rank=len(results) + 1,
                score=item.score,
                section_id=section_id,
                section_path=section_path,
                window=window,
            )
        )
        if len(results) >= top_k:
            break

    return results


def _policy() -> ToolPolicy:
    return ToolPolicy(
        expose_by_default=False,
        selection_mode=ToolSelectionMode.CONTEXTUAL,
        persist_output=True,
        risk_level=ToolRiskLevel.LOW,
        required_context_keys=("session_id",),
        timeout_seconds=_TIMEOUT_SECONDS,
    )
