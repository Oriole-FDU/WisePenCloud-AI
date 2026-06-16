from __future__ import annotations

from dataclasses import dataclass

from chat.application.tools.core.tool_return import (
    SuggestedAction,
    SuggestedActionPriority,
    ToolReturn,
)
from .providers.models import ProviderSearchResponse
from .service import WebSearchResult


@dataclass(frozen=True, slots=True)
class WebSearchCandidate:
    result_id: str  # [1] 形式的稳定候选编号，供后续模型引用
    title: str
    url: str
    overview: str | None = None
    highlights: tuple[str, ...] = ()
    supplier_answer: str | None = None  # provider 直答，仅作为检索提示


def build_candidates(
    responses: tuple[ProviderSearchResponse, ...],
) -> tuple[WebSearchCandidate, ...]:
    """从 provider 响应构建候选列表，使用 [1]、[2] 形式的稳定编号。"""
    flat = (item for resp in responses for item in resp.results)
    return tuple(
        WebSearchCandidate(
            result_id=f"[{i}]",
            title=item.title,
            url=item.url,
            overview=item.preview.overview,
            highlights=item.preview.highlights,
            supplier_answer=item.preview.answer,
        )
        for i, item in enumerate(flat, start=1)
    )


def build_web_search_tool_return(
    result: WebSearchResult,
    *,
    candidates: tuple[WebSearchCandidate, ...],
    recommended_ids: tuple[str, ...] = (),
    warning: str | None = None,
) -> ToolReturn:
    """组装 web_search 的可见返回。

    - candidates：完整候选列表（含 supplier_answer）。
    - recommended_ids：按优先级排序的候选编号，最多 5 个；不足时为空或更短。
    - supplier_answer 仅作为检索提示，模型必须自行 fetch 后再作为证据使用。
    """
    visible_result: dict[str, object] = {
        "query": result.query,
        "candidates": candidates,
        "recommended_ids": recommended_ids,
        "suggested_action": SuggestedAction(
            tool_name="web_fetch",
            mode="fetch_result_url",
            reason=(
                "Fetch selected candidate URLs before using them as evidence. "
                "supplier_answer is only a retrieval hint and must not replace your own fetch and analysis."
            ),
            priority=SuggestedActionPriority.HIGH,
        ),
    }
    if warning:
        visible_result["warning"] = warning

    return ToolReturn(
        tag="web_search_result",
        visible_result=visible_result,
        cacheable_texts=(),  # 明确不缓存
    )
