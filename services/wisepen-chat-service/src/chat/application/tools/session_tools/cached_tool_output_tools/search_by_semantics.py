from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from common.utils.ranking import RankCandidate, RankingPipeline, RankQuery, RankRequest
from common.utils.ranking.fusion import WeightedRrfFusion
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
_DEFAULT_TOP_K = 5
_MAX_TOP_K = 5
_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 64,
            "description": "One or more cached tool output content_id values returned in previous tool results.",
        },
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "Complete question the semantic source chunks should answer.",
        },
        "top_k": {
            "type": "integer",
            "default": _DEFAULT_TOP_K,
            "minimum": 1,
            "maximum": _MAX_TOP_K,
            "description": "Maximum globally ranked semantic chunks returned; at most 5.",
        },
    },
    "required": ["content_ids", "query"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class CachedToolOutputSearchBySemanticsItem:
    content_id: str
    rank: int
    score: float
    chunk_index: int
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
                model=settings.RERANKER_MODEL
            ),
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
                    "physical page boundaries. Each result includes a bounded source window and "
                    "absolute offsets for deterministic follow-up reads.\n\n"
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
        # semantic search 必须有自然语言查询，空查询无法构建排序请求。
        query = kwargs["query"].strip()
        if not query:
            raise ToolExecutionError(
                reason="missing_query",
                detail_reason="query must be non-empty.",
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
                query=query,
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
    query: str,
    top_k: int,
    ranking_pipeline: RankingPipeline,
) -> list[CachedToolOutputSearchBySemanticsItem]:
    candidates: list[RankCandidate] = []
    sources: dict[str, tuple[StoredCachedToolOutput, CachedToolOutputChunk]] = {}
    for stored in stored_items:
        sections_by_id = {
            section.section_id: section for section in stored.sections
        }
        for chunk in stored.chunks:
            # 排序候选从 chunk 的原文 span 回读，避免只拿 locator 元数据参与语义检索。
            text = chunk.text
            if not text:
                continue
            content_id = stored.content_id
            candidate_id = f"{content_id}:chunk:{chunk.chunk_index}"
            # sources 保存候选和原文 chunk 的映射，排序完成后再构造可读窗口。
            sources[candidate_id] = (stored, chunk)
            candidates.append(
                RankCandidate(
                    candidate_id=candidate_id,
                    text=text,
                    fields={
                        # section_path 只在缓存内部辅助排序，对外窗口仅暴露 ID。
                        "section": "\n".join(
                            " > ".join(sections_by_id[section_id].section_path)
                            for section_id in chunk.section_ids
                            if section_id in sections_by_id
                        ),
                        "anchor": "\n".join(chunk.anchor_labels),
                    },
                    group_key=content_id,
                )
            )

    if not candidates:
        # 没有候选时，返回空检索结果。
        return []

    # RankingPipeline 的请求模型要求 candidates 为 tuple，这里是外部契约不是内部数组语义。
    result = await ranking_pipeline.arank(
        RankRequest(
            query=RankQuery(text=query),
            candidates=tuple(candidates),
            top_k=top_k,
            candidate_limit=len(candidates),
        )
    )

    results: list[CachedToolOutputSearchBySemanticsItem] = []
    for item in result.ranked:
        source = sources.get(item.candidate_id)
        if source is None:
            continue
        stored, chunk = source
        content_id = stored.content_id
        # chunk 在缓存写入时已经按固定上限切好；搜索只把它投影成通用续读窗口。
        window = CachedToolOutputWindow(
            text=chunk.text,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            truncated=False,
        )
        results.append(
            CachedToolOutputSearchBySemanticsItem(
                content_id=content_id,
                rank=item.rank,
                score=item.score,
                chunk_index=chunk.chunk_index,
                window=window,
            )
        )

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
