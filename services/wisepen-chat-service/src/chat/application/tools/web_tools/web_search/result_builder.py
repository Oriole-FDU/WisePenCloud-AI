from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from chat.application.tools.core.tool_return import (
    SuggestedAction,
    SuggestedActionPriority,
    SuggestedActions,
    ToolReturn,
)
from .candidate_store import WebSearchCandidateMapping
from .providers.models import ProviderSearchResponse
from .runtime_context import WebSearchMode, WebSearchRuntimeConfig


@dataclass(frozen=True, slots=True)
class WebSearchCandidate:
    search_ref: str
    search_run_id: str
    candidate_id: str  # [1] 形式的稳定候选编号，供后续模型引用
    source_id: str
    title: str
    url: str
    source_scope: str
    overview: str | None = None
    highlights: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VisibleWebSearchCandidate:
    search_ref: str
    title: str
    overview: str | None = None
    highlights: tuple[str, ...] = ()


def build_candidates(
    responses: tuple[ProviderSearchResponse, ...],
    *,
    search_config: WebSearchRuntimeConfig,
) -> tuple[WebSearchCandidate, ...]:
    """从 provider 响应构建候选列表，使用 [1]、[2] 形式的稳定编号。"""
    search_run_id = f"srch_{uuid.uuid4().hex[:16]}"
    return tuple(
        WebSearchCandidate(
            search_ref=f"r{uuid.uuid4().hex[:10]}",
            search_run_id=search_run_id,
            candidate_id=f"[{i}]",
            source_id=resp.source_id or search_config.source_id,
            title=item.title,
            url=item.url,
            source_scope="web_custom" if search_config.search_mode == WebSearchMode.CUSTOM else "web_public",
            overview=item.preview.overview,
            highlights=item.preview.highlights,
        )
        for i, (resp, item) in enumerate(
            ((resp, item) for resp in responses for item in resp.results),
            start=1,
        )
    )


def build_web_search_tool_return(
    result: Any,
    *,
    candidates: tuple[WebSearchCandidate, ...],
    responses: tuple[ProviderSearchResponse, ...],
    recommended_ids: tuple[str, ...] = (),
    final_query: str = "",
    warning: str | None = None,
) -> ToolReturn:
    """组装 web_search 的可见返回。

    - candidates：完整候选列表。
    - supplier_answers：供应商对 query 的直答（去重），仅作为检索提示。
    - final_query：多跳最终使用的查询词。
    - recommended_ids：按优先级排序的候选编号，最多 5 个。
    - warning：多跳未收敛时的警告，触发降级策略。
    """
    supplier_answers = tuple(dict.fromkeys(r.answer for r in responses if r.answer))

    # 根据是否存在 warning 构建不同的建议行为
    if warning:
        suggested_actions = SuggestedActions(
            suggested_actions=(
                SuggestedAction(
                    tool_name="web_search",
                    reason=(
                        f"Previous search did not converge: {warning} "
                        "You SHOULD rephrase the query, narrow the scope, or split into "
                        "multiple more specific parallel searches to improve coverage."
                    ),
                    priority=SuggestedActionPriority.HIGH,
                ),
                SuggestedAction(
                    tool_name="web_fetch",
                    mode="from_search_results",
                    reason=(
                        "Fetch selected search refs before using them as evidence. "
                        "Priority lowered because search did not fully converge — "
                        "consider re-searching first."
                    ),
                    priority=SuggestedActionPriority.MEDIUM,
                ),
            ),
        )
    else:
        suggested_actions = SuggestedActions(
            suggested_actions=(
                SuggestedAction(
                    tool_name="web_fetch",
                    mode="from_search_results",
                    reason=(
                        "Fetch selected search refs before using them as evidence. "
                        "supplier_answers are only retrieval hints and must not replace your own fetch and analysis."
                    ),
                    priority=SuggestedActionPriority.HIGH,
                ),
                SuggestedAction(
                    tool_name="paper_hydrate",
                    reason=(
                        "Use only when a candidate is clearly a paper and you need finer metadata such as "
                        "authors, venue, abstract, cited_by_count, or open access."
                    ),
                    priority=SuggestedActionPriority.LOW,
                ),
                SuggestedAction(
                    tool_name="github_hydrate",
                    reason=(
                        "Use only when a candidate is clearly a GitHub repository and you need finer metadata such as "
                        "topics, license, stars, forks, or update time."
                    ),
                    priority=SuggestedActionPriority.LOW,
                ),
            ),
        )

    visible_result: dict[str, object] = {
        "query": result.query,
        "candidates": tuple(
            VisibleWebSearchCandidate(
                search_ref=candidate.search_ref,
                title=candidate.title,
                overview=candidate.overview,
                highlights=candidate.highlights,
            )
            for candidate in candidates
        ),
        "recommended_ids": recommended_ids,
        "suggested_actions": suggested_actions,
    }
    if final_query and final_query != result.query:
        visible_result["final_query"] = final_query
    if supplier_answers:
        visible_result["supplier_answers"] = supplier_answers
    if warning:
        visible_result["warning"] = warning

    return ToolReturn(
        tag="web_search_result",
        visible_result=visible_result,
        cacheable_texts=(),  # 明确不缓存
    )


def build_candidate_mappings(
    candidates: tuple[WebSearchCandidate, ...],
    *,
    user_id: str,
) -> tuple[WebSearchCandidateMapping, ...]:
    return tuple(
        WebSearchCandidateMapping(
            user_id=user_id,
            search_ref=candidate.search_ref,
            search_run_id=candidate.search_run_id,
            candidate_id=candidate.candidate_id,
            source_id=candidate.source_id,
            url=candidate.url,
            source_scope=candidate.source_scope,
            metadata={"title": candidate.title},
        )
        for candidate in candidates
    )
