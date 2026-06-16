from __future__ import annotations

from dataclasses import replace
from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.core.tool_return import ToolReturn
from chat.application.tools.web_tools.web_search.errors import (
    WebSearchCustomApiKeyInvalid,
    WebSearchCustomApiKeyMissing,
    WebSearchEmptyResult,
    WebSearchError,
    WebSearchNetworkError,
)
from chat.application.tools.web_tools.web_search.multi_hop import (
    AnswerSufficiency,
    judge_answer_sufficiency,
    rank_candidate_ids,
)
from chat.application.tools.web_tools.web_search.result_builder import (
    WebSearchCandidate,
    build_candidates,
    build_web_search_tool_return,
)
from chat.application.tools.web_tools.web_search.service import (
    WebSearchResult,
    WebSearchService,
)

DEFAULT_MAX_HOPS = 3
MAX_WEB_SEARCH_RESULTS = 10
MAX_SUFFICIENCY_TEXT_CHARS = 12000
MAX_RECOMMENDED_CANDIDATES = 5
FALLBACK_CANDIDATES_COUNT = 3

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Required. The user's original information need, in the user's own language. "
                "MUST be a non-empty string. Do NOT paraphrase into a search query here; "
                "use first_query / fallback_query for search phrasings."
            ),
        },
        "first_query": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Required. The primary search query, executed on the first hop. "
                "MUST be a concise, search-engine-friendly phrasing. "
                "Invalid: passing the raw `question` verbatim; passing a full natural-language sentence. "
                "Example: question='苹果最新财报利润' -> first_query='Apple Q4 2025 earnings net profit'."
            ),
        },
        "fallback_query": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Required. A backup search query used when first_query is insufficient. "
                "MUST differ from first_query in interpretation angle OR language. "
                "Invalid: identical or near-identical to first_query; same angle in the same language. "
                "Example: first_query='Apple Q4 2025 earnings net profit' -> "
                "fallback_query='苹果 2025 财年第四季度 净利润' (different language) or "
                "fallback_query='AAPL quarterly income statement' (different angle)."
            ),
        },
        "max_results": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_WEB_SEARCH_RESULTS,
            "default": 5,
            "description": "Maximum candidate results per search request. SHOULD be left at default unless the user needs breadth.",
        },
    },
    "required": ["question", "first_query", "fallback_query"],
    "additionalProperties": False,
}


class WebSearchTool:
    """Web search 工具门面，内部处理路由和轻量多跳。"""

    __slots__ = ("_definition", "_max_hops", "_service")

    def __init__(
        self,
        *,
        service: WebSearchService,
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> None:
        self._service = service
        self._max_hops = min(max(1, max_hops), DEFAULT_MAX_HOPS)
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="web_search",
                description=(
                    "Search the web for candidate pages and return ranked candidates.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when the user needs real-time or external information not present in context.\n"
                    "  - SHOULD trigger for fact-checking or verifying claims against external sources.\n"
                    "  - SHOULD trigger when the user explicitly asks to search or browse the web.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - The answer is already available in the conversation context or attached knowledge base.\n"
                    "  - The question is pure common knowledge with no time-sensitivity and the user does not request a source.\n"
                    "\n"
                    "INTERNAL FLOW (you do not control this, but it affects how you SHOULD construct inputs):\n"
                    "  1. first_query is executed first.\n"
                    "  2. If insufficient, a small internal model rewrites the query for the next hop.\n"
                    "  3. If still insufficient and no rewrite is produced, fallback_query is used once.\n"
                    "  4. After up to 3 hops, candidates are ranked: if sufficient, a small model returns up to 5 ranked ids; otherwise the first 3 candidates in original order are returned.\n"
                    "  => Therefore first_query and fallback_query MUST cover different angles or languages so the multi-hop has real coverage to work with.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - first_query and fallback_query MUST NOT be identical or near-identical strings.\n"
                    "  - fallback_query MUST differ from first_query in interpretation angle OR language.\n"
                    "  - Do NOT pass question text verbatim as both queries; rephrase for each.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - supplier_answer fields are ONLY retrieval hints; you MUST fetch URLs via web_fetch before using any result as evidence.\n"
                    "  - recommended_ids is a priority hint, not a guarantee of correctness; verify by fetching.\n"
                    "  - If web_search fails (network/quota/empty), inform the user; do NOT silently answer from parametric memory.\n"
                    "  - Within one session, do NOT re-issue web_search for the same question unless new information is required.\n"
                ),
                parameters_schema=ToolParametersSchema(PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                timeout_seconds=45.0,
                cache_chunked=False,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> ToolReturn:
        question = str(kwargs["question"]).strip()
        first_query = str(kwargs["first_query"]).strip()
        fallback_query = str(kwargs["fallback_query"]).strip()
        max_results = int(kwargs.get("max_results") or 5)

        if not question:
            raise ToolExecutionError(
                reason="missing_question",
                detail_reason="question must be a non-empty string.",
                retryable=False,
            )
        if not first_query:
            raise ToolExecutionError(
                reason="invalid_first_query",
                detail_reason="first_query must be a non-empty string.",
                retryable=False,
            )
        if not fallback_query:
            raise ToolExecutionError(
                reason="invalid_fallback_query",
                detail_reason="fallback_query must be a non-empty string.",
                retryable=False,
            )

        try:
            merged_result, sufficiency, candidates = await self._run_multi_hop(
                question=question,
                first_query=first_query,
                fallback_query=fallback_query,
                max_results=max(1, min(max_results, MAX_WEB_SEARCH_RESULTS)),
            )
        except WebSearchCustomApiKeyMissing as exc:
            raise ToolExecutionError(
                reason="web_search_api_key_missing",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
        except WebSearchCustomApiKeyInvalid as exc:
            raise ToolExecutionError(
                reason="web_search_api_key_invalid",
                detail_reason=str(exc),
                retryable=False,
            ) from exc
        except WebSearchNetworkError as exc:
            raise ToolExecutionError(
                reason="web_search_network_error",
                detail_reason=str(exc),
                retryable=True,
            ) from exc
        except WebSearchEmptyResult as exc:
            raise ToolExecutionError(
                reason="web_search_empty_result",
                detail_reason=str(exc),
                retryable=True,
            ) from exc
        except WebSearchError as exc:
            raise ToolExecutionError(
                reason="web_search_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc

        warning = None
        if sufficiency is not None and not sufficiency.sufficient:
            warning = f"多跳结束，仍然没有足够的结果：{sufficiency.reason}"

        recommended_ids = await self._select_recommended_ids(
            question=question,
            candidates=candidates,
            sufficiency=sufficiency,
        )

        return build_web_search_tool_return(
            merged_result,
            candidates=candidates,
            recommended_ids=recommended_ids,
            warning=warning,
        )

    async def _run_multi_hop(
        self,
        *,
        question: str,
        first_query: str,
        fallback_query: str,
        max_results: int,
    ) -> tuple[WebSearchResult, AnswerSufficiency | None, tuple[WebSearchCandidate, ...]]:
        """执行多跳搜索，返回合并结果、最后一次充分性判断和候选列表。

        - 第一跳使用 first_query；后续若不足，优先使用小模型给出的 next_query，
          否则回退到 fallback_query。
        - 候选列表在多跳结束后统一构建，编号在所有结果中保持稳定。
        """
        results: list[WebSearchResult] = []
        sufficiency: AnswerSufficiency | None = None
        fallback_remaining = True

        query = first_query
        while len(results) < self._max_hops:
            results.append(await self._service.search(query=query, max_results=max_results))
            sufficiency = await judge_answer_sufficiency(
                question=question,
                current_text=_search_context_text(results),
            )

            if sufficiency.sufficient:
                break
            if sufficiency.next_query:
                query = sufficiency.next_query
            elif fallback_remaining:
                query = fallback_query
                fallback_remaining = False
            else:
                break

        merged = _merge_results(question=question, results=results)
        candidates = build_candidates(merged.responses)
        return merged, sufficiency, candidates

    async def _select_recommended_ids(
        self,
        *,
        question: str,
        candidates: tuple[WebSearchCandidate, ...],
        sufficiency: AnswerSufficiency | None,
    ) -> tuple[str, ...]:
        """选择推荐候选编号。

        - 若最后一次多跳判定为足够回答：再次调用小模型对候选按相关性排序，最多 5 个。
        - 若判定不足：按搜索原始顺序返回前 3 个候选编号。
        - 小模型排序失败时回退到原始顺序的前 3 个。
        """
        if not candidates:
            return ()

        if sufficiency is not None and sufficiency.sufficient:
            ranked = await rank_candidate_ids(
                question=question,
                candidates_text=_candidates_text(candidates),
            )
            if ranked:
                valid_ids = {candidate.result_id for candidate in candidates}
                filtered = tuple(
                    candidate_id
                    for candidate_id in ranked
                    if candidate_id in valid_ids
                )[:MAX_RECOMMENDED_CANDIDATES]
                if filtered:
                    return filtered

        # 不足或排序失败：回退到原始顺序的前 3 个
        return tuple(
            candidate.result_id
            for candidate in candidates[:FALLBACK_CANDIDATES_COUNT]
        )


def _merge_results(*, question: str, results: list[WebSearchResult]) -> WebSearchResult:
    if not results:
        raise ToolExecutionError(
            reason="web_search_no_result",
            detail_reason="web_search did not execute any search request.",
            retryable=True,
        )

    first = results[0]
    responses = tuple(response for result in results for response in result.responses)
    return replace(
        first,
        query=question,
        responses=responses,
    )


def _search_context_text(results: list[WebSearchResult]) -> str:
    lines: list[str] = []
    for result in results:
        for response in result.responses:
            for item in response.results:
                parts = [
                    f"title: {item.title}",
                    f"url: {item.url}",
                ]
                if item.preview.overview:
                    parts.append(f"overview: {item.preview.overview}")
                if item.preview.highlights:
                    parts.append("highlights: " + " | ".join(item.preview.highlights))
                if item.preview.answer:
                    parts.append(f"supplier_answer: {item.preview.answer}")
                lines.append("\n".join(parts))
    return "\n\n".join(lines)[:MAX_SUFFICIENCY_TEXT_CHARS]


def _candidates_text(candidates: tuple[WebSearchCandidate, ...]) -> str:
    """构建给小模型排序用的候选文本，包含编号、标题、URL、overview、highlights、supplier_answer。"""
    lines: list[str] = []
    for candidate in candidates:
        parts = [
            f"id: {candidate.result_id}",
            f"title: {candidate.title}",
            f"url: {candidate.url}",
        ]
        if candidate.overview:
            parts.append(f"overview: {candidate.overview}")
        if candidate.highlights:
            parts.append("highlights: " + " | ".join(candidate.highlights))
        if candidate.supplier_answer:
            parts.append(f"supplier_answer: {candidate.supplier_answer}")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)
