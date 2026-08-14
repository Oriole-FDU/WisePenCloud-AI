from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

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
    ToolContentChunk as CachedToolOutputChunk,
    ToolContentStore as CachedToolOutputStore,
)
from chat.core.config.app_settings import settings

from chat.application.tools.session_tools.cached_tool_output_tools.window import CachedToolOutputWindow, CachedToolOutputWindowBuilder
from common.utils.ranking import RankCandidate, RankQuery, RankRequest
from common.utils.ranking import RankingPipeline
from common.utils.ranking.fusion import WeightedRrfFusion
from common.utils.ranking.rerankers import ZeroEntropyReranker, ZeroEntropyRerankerConfig
from common.utils.ranking.scorers import BM25Scorer, FieldedBM25Scorer, FieldedBM25ScorerConfig
from common.utils.ranking.tokenizer import ThuLacRankingTokenizer

_TIMEOUT_SECONDS = 300.0
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
            "default": 10,
            "minimum": 0,
            "description": "Maximum globally ranked semantic chunks returned.",
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


@dataclass(slots=True)
class CachedToolOutputSearchBySemanticsResult:
    results: list[CachedToolOutputSearchBySemanticsItem] = field(default_factory=list)
    budget_exhausted: bool = False

def build_cached_tool_output_search_by_semantics_pipeline() -> RankingPipeline:
    tokenizer = ThuLacRankingTokenizer()
    return RankingPipeline(
        scorers=(
            BM25Scorer(tokenizer=tokenizer),
            FieldedBM25Scorer(
                tokenizer=tokenizer,
                config=FieldedBM25ScorerConfig(
                    field_weights={"section": 2.0, "anchor": 1.5},
                ),
            ),
        ),
        fusion=WeightedRrfFusion(),
        reranker=ZeroEntropyReranker(
            client=AsyncZeroEntropy(api_key=settings.ZERO_ENTROPY_API_KEY),
            config=ZeroEntropyRerankerConfig(model=settings.RERANKER_MODEL),
        ),
    )

class CachedToolOutputSearchBySemanticsTool:
    __slots__ = ("_definition", "_ranking_pipeline", "_store")

    def __init__(
        self,
        *,
        store: CachedToolOutputStore,
    ) -> None:
        self._store = store
        self._ranking_pipeline = build_cached_tool_output_search_by_semantics_pipeline()
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="search_cached_tool_output_by_semantics",
                description=(
                    "Search semantic chunks from one or more cached tool outputs and return the most "
                    "relevant source windows. Chunks follow Markdown section semantics rather than "
                    "physical page boundaries. Each result includes known page, section, and anchor "
                    "metadata for deterministic follow-up reads.\n\n"
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
    ) -> CachedToolOutputSearchBySemanticsResult:
        del config
        # semantic search 必须有自然语言查询，空查询无法构建排序请求。
        query = str(kwargs.get("query") or "").strip()
        if not query:
            raise ToolExecutionError(
                reason="missing_query",
                detail_reason="query is required.",
            )
        try:
            # 允许多个 content 混排；不存在的 content 不参与候选构建。
            stored_items = []
            session_id = str(context["session_id"])
            for content_id in [str(value) for value in kwargs["content_ids"]]:
                stored = await self._store.get(
                    content_id=content_id,
                    session_id=session_id,
                )
                if stored is not None:
                    stored_items.append(stored)
            return await _search_by_semantics(
                stored_items=stored_items,
                query=query,
                top_k=max(int(kwargs.get("top_k", 10)), 0),
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
) -> CachedToolOutputSearchBySemanticsResult:
    candidates: list[RankCandidate] = []
    sources: dict[str, tuple[StoredCachedToolOutput, CachedToolOutputChunk]] = {}
    for stored in stored_items:
        for chunk in stored.chunks:
            # 排序候选从 chunk 的原文 span 回读，避免只拿 locator 元数据参与语义检索。
            text = _chunk_text(stored, chunk)
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
                        "section": "\n".join(
                            " > ".join(path) for path in chunk.section_paths
                        ),
                        "anchor": "\n".join(chunk.anchor_labels),
                    },
                    group_key=content_id,
                )
            )

    if not candidates or top_k <= 0:
        # 没有候选或调用方要求 0 条结果时，返回空检索结果。
        return CachedToolOutputSearchBySemanticsResult()

    # RankingPipeline 的请求模型要求 candidates 为 tuple，这里是外部契约不是内部数组语义。
    result = await ranking_pipeline.arank(
        RankRequest(
            query=RankQuery(text=query.strip()),
            candidates=tuple(candidates),
            top_k=top_k,
            candidate_limit=len(candidates),
        )
    )

    # semantic 结果使用独立窗口预算，避免检索结果一次塞入过多原文。
    builder = CachedToolOutputWindowBuilder(
        char_budget=settings.TOOL_CONTENT_SEMANTIC_SEARCH_WINDOW_CHAR_BUDGET
    )
    results: list[CachedToolOutputSearchBySemanticsItem] = []
    remaining = settings.TOOL_CONTENT_SEMANTIC_SEARCH_TOTAL_CHAR_BUDGET
    budget_exhausted = False
    for item in result.ranked:
        if remaining <= 0:
            # 排序结果还有剩余，但返回预算耗尽时停止构造窗口。
            budget_exhausted = True
            break
        source = sources.get(item.candidate_id)
        if source is None:
            continue
        stored, chunk = source
        content_id = stored.content_id
        # 排名命中后再回原文构造窗口，保证结果带 page/section/anchor 元数据。
        window = builder.build_source_window(
            stored,
            chunk=chunk,
            char_budget=remaining,
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
        remaining -= len(window.text)

    return CachedToolOutputSearchBySemanticsResult(
        results=results,
        budget_exhausted=budget_exhausted,
    )


def _chunk_text(stored: StoredCachedToolOutput, chunk: CachedToolOutputChunk) -> str:
    # chunk 可能由多个不连续 span 组成，用空行连接后交给 ranking pipeline。
    return "\n\n".join(
        stored.text[span.start_offset : span.end_offset].strip()
        for span in chunk.source_spans
    )


def _policy() -> ToolPolicy:
    return ToolPolicy(
        expose_by_default=False,
        selection_mode=ToolSelectionMode.CONTEXTUAL,
        persist_output=True,
        risk_level=ToolRiskLevel.LOW,
        required_context_keys=("session_id",),
        timeout_seconds=_TIMEOUT_SECONDS,
    )
