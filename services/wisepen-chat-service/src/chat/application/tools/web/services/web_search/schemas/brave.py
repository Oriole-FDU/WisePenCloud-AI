from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping

from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.models import SearchResponse, SearchResult
from chat.application.tools.web.services.web_search.utils.results import deduplicate_results_by_domain, is_valid_result


@dataclass(frozen=True, slots=True)
class BraveSearchRequest:
    """Brave Search API 请求体。

    to_params 对应 HTTP GET 查询参数（q, count）。
    """

    query: str
    count: int

    def to_params(self) -> Dict[str, Any]:
        """转为 HTTP GET 查询参数（key 映射为 Brave API 字段名）。"""
        return {
            "q": self.query,
            "count": str(self.count),
        }


def map_brave_response(
        data: Mapping[str, Any],
        *,
        query: str,
        max_results: int,
) -> SearchResponse:
    web_data = data.get("web") or {}
    raw_items = web_data.get("results") if isinstance(web_data, Mapping) else []

    if not isinstance(raw_items, list):
        raw_items = []

    results: List[SearchResult] = []

    for item in raw_items:

        if not isinstance(item, Mapping):
            continue

        result = SearchResult(
            title=str(item.get("title") or "").strip(),
            url=str(item.get("url") or "").strip(),
            snippet=str(item.get("description") or "").strip(),
        )

        if is_valid_result(result):
            results.append(result)

    results = deduplicate_results_by_domain(results, max_per_domain=2)

    return SearchResponse(
        query=query,
        results=results[:max_results],
        source=SearcherName.BRAVE,
    )
