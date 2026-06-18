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
from chat.application.tools.web_tools.web_search.candidate_store.repository import (
    WebSearchCandidateRepository,
)
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
from chat.application.tools.web_tools.web_search.providers.models import (
    ProviderSearchResponse,
)
from chat.application.tools.web_tools.web_search.result_builder import (
    WebSearchCandidate,
    build_candidate_mappings,
    build_candidates,
    build_web_search_tool_return,
)
from chat.application.tools.web_tools.web_search.runtime_context import (
    WebSearchMode,
    WebSearchRuntimeConfig,
)
from chat.application.tools.web_tools.web_search.service import (
    WebSearchCustomSource,
    WebSearchCustomSourceFactory,
    WebSearchResult,
    WebSearchService,
)

# 边界控制常量
DEFAULT_MAX_HOPS = 3
DEFAULT_WEB_SEARCH_RESULTS = 10
MAX_WEB_SEARCH_RESULTS = 20
MAX_SUFFICIENCY_TEXT_CHARS = 12000
MAX_RECOMMENDED_CANDIDATES = 5
FALLBACK_CANDIDATES_COUNT = 3

# 大模型 Function Calling 参数契约（保持英文描述以确保模型理解的精确度）
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
            "default": DEFAULT_WEB_SEARCH_RESULTS,
            "description": "Maximum candidate results per search request. SHOULD be left at default unless the user needs breadth.",
        },
    },
    "required": ["question", "first_query", "fallback_query"],
    "additionalProperties": False,
}


class WebSearchTool:
    """Web 搜索工具门面：对外封装纯英文元数据契约，对内编排轻量多跳循环检索流。"""

    __slots__ = ("_candidate_repository", "_candidate_ttl_seconds", "_custom_source_factory", "_definition",
                 "_max_hops", "_service")

    def __init__(
            self,
            *,
            service: WebSearchService,
            custom_source_factory: WebSearchCustomSourceFactory,
            candidate_repository: WebSearchCandidateRepository,
            candidate_ttl_seconds: int = 3600,
            max_hops: int = DEFAULT_MAX_HOPS,
    ) -> None:
        self._service = service
        self._custom_source_factory = custom_source_factory
        self._candidate_repository = candidate_repository
        self._candidate_ttl_seconds = candidate_ttl_seconds
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
                    "  - supplier_answers is ONLY a retrieval hint; you MUST fetch URLs via web_fetch before using any result as evidence.\n"
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
                required_context_keys=("user_id", "session_id", "search_config"),
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> ToolReturn:
        """解析引擎主执行切面。处理多维领域异常并转换为安全的标准运行时错误。"""
        question = kwargs["question"].strip()
        first_query = kwargs["first_query"].strip()
        fallback_query = kwargs["fallback_query"].strip()
        max_results = kwargs.get("max_results") or DEFAULT_WEB_SEARCH_RESULTS

        search_config = context["search_config"]

        try:
            # 1. 动态凭证安全识别
            custom_source = self._custom_source_from_context(search_config)

            # 2. 调度执行轻量级多跳收敛循环
            merged_result, sufficiency, candidates, final_query = await self._run_multi_hop(
                question=question,
                first_query=first_query,
                fallback_query=fallback_query,
                max_results=max(1, min(max_results, MAX_WEB_SEARCH_RESULTS)),
                custom_source=custom_source,
                search_config=search_config,
            )

            # 3. 持久化注册引用映射关系，供后续二次 fetch 溯源
            await self._store_candidate_mappings(
                user_id=str(context["user_id"]),
                candidates=candidates,
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

        # 多跳完结判定：若最终仍未收敛，挂载警告视窗
        warning = f"Multi-hop search did not converge: {sufficiency.reason}" if sufficiency and not sufficiency.sufficient else None

        # 4. 交叉计算推荐展示结果
        recommended_ids = await self._select_recommended_ids(
            question=question,
            candidates=candidates,
            responses=merged_result.responses,
            sufficiency=sufficiency,
        )

        return build_web_search_tool_return(
            merged_result,
            candidates=candidates,
            responses=merged_result.responses,
            recommended_ids=recommended_ids,
            final_query=final_query,
            warning=warning,
        )

    async def _run_multi_hop(
            self,
            *,
            question: str,
            first_query: str,
            fallback_query: str,
            max_results: int,
            custom_source: WebSearchCustomSource | None,
            search_config: WebSearchRuntimeConfig,
    ) -> tuple[WebSearchResult, AnswerSufficiency | None, tuple[WebSearchCandidate, ...], str]:
        """轻量级多跳迭代控制流。返回合并结果、充分性判断、候选列表和最终使用的查询词。"""
        results: list[WebSearchResult] = []
        sufficiency: AnswerSufficiency | None = None
        fallback_remaining = True

        query = first_query
        while len(results) < self._max_hops:
            # 执行单步物理搜索
            res = await self._service.search(
                query=query,
                max_results=max_results,
                custom_source=custom_source,
                platform_provider=search_config.provider,
            )
            results.append(res)

            # 判断当前累积上下文的信息充足度
            sufficiency = await judge_answer_sufficiency(
                question=question,
                current_text=_search_context_text(results),
            )

            if sufficiency.sufficient:
                break

            # 动态调整下一跳的意图查询词
            if sufficiency.next_query:
                query = sufficiency.next_query
            elif fallback_remaining:
                query = fallback_query
                fallback_remaining = False
            else:
                break

        # 合并多路搜索历史并统一编号归一化
        merged = _merge_results(question=question, results=results)
        candidates = build_candidates(merged.responses, search_config=search_config)
        return merged, sufficiency, candidates, query

    async def _select_recommended_ids(
            self,
            *,
            question: str,
            candidates: tuple[WebSearchCandidate, ...],
            responses: tuple[ProviderSearchResponse, ...],
            sufficiency: AnswerSufficiency | None,
    ) -> tuple[str, ...]:
        """交叉相关性重排过滤。"""
        if not candidates:
            return ()

        # 主干路径：若多跳信息充足，调度微型模型计算相关性精排，截取前 5
        if sufficiency and sufficiency.sufficient:
            ranked = await rank_candidate_ids(
                question=question,
                candidates_text=_candidates_text(candidates, responses=responses),
            )
            if ranked:
                valid_ids = {c.candidate_id for c in candidates}
                filtered = tuple(cid for cid in ranked if cid in valid_ids)[:MAX_RECOMMENDED_CANDIDATES]
                if filtered:
                    return filtered

        # 退化兜底路径：信息不足或精排失败，直接默认裁剪原始顺序的前 3 个
        return tuple(c.candidate_id for c in candidates[:FALLBACK_CANDIDATES_COUNT])

    def _custom_source_from_context(self, search_config: WebSearchRuntimeConfig) -> WebSearchCustomSource | None:
        """上下文自定义凭证工厂构建拦截器。"""
        if search_config.search_mode == WebSearchMode.PLATFORM:
            return None
        if not search_config.is_valid:
            raise WebSearchCustomApiKeyInvalid(
                provider=search_config.provider,
                reason=search_config.error_message or "custom 搜索配置不可用",
            )
        return self._custom_source_factory.build(search_config)

    async def _store_candidate_mappings(
            self,
            *,
            user_id: str,
            candidates: tuple[WebSearchCandidate, ...],
    ) -> None:
        """批量注册映射中间件，维护运行时生存周期 TTL。"""
        for mapping in build_candidate_mappings(candidates, user_id=user_id):
            await self._candidate_repository.set_mapping(mapping, ttl_seconds=self._candidate_ttl_seconds)


# ==========================================
# 私有辅助函数 (Private Helper Functions)
# ==========================================

def _merge_results(*, question: str, results: list[WebSearchResult]) -> WebSearchResult:
    """将多条多跳搜索响应的 Providers 结果扁平化重组。"""
    if not results:
        raise ToolExecutionError(
            reason="web_search_no_result",
            detail_reason="web_search did not execute any search request.",
            retryable=True,
        )
    return replace(results[0], query=question, responses=tuple(r for res in results for r in res.responses))


def _search_context_text(results: list[WebSearchResult]) -> str:
    """将搜索结果矩阵序列化为用于判断充足度的紧凑文本块。"""
    lines: list[str] = []
    for result in results:
        for response in result.responses:
            if response.answer:
                lines.append(f"supplier_answer: {response.answer}")
            for item in response.results:
                parts = [f"title: {item.title}", f"url: {item.url}"]
                if item.preview.overview:
                    parts.append(f"overview: {item.preview.overview}")
                if item.preview.highlights:
                    parts.append("highlights: " + " | ".join(item.preview.highlights))
                lines.append("\n".join(parts))
    return "\n\n".join(lines)[:MAX_SUFFICIENCY_TEXT_CHARS]


def _candidates_text(
        candidates: tuple[WebSearchCandidate, ...],
        *,
        responses: tuple[ProviderSearchResponse, ...],
) -> str:
    """构建用于微型排序模型的标准文本序列（含去重直答提示）。"""
    unique_answers = dict.fromkeys(r.answer for r in responses if r.answer)
    lines: list[str] = []

    if unique_answers:
        lines.append("supplier_answer:\n" + "\n".join(f"- {a}" for a in unique_answers))

    for candidate in candidates:
        parts = [f"id: {candidate.candidate_id}", f"title: {candidate.title}", f"url: {candidate.url}"]
        if candidate.overview:
            parts.append(f"overview: {candidate.overview}")
        if candidate.highlights:
            parts.append("highlights: " + " | ".join(candidate.highlights))
        lines.append("\n".join(parts))

    return "\n\n".join(lines)