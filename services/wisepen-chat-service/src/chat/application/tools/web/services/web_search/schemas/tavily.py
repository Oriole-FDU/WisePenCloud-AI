from dataclasses import dataclass
from typing import Any, Dict, List, Mapping

from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.models import SearchResponse, SearchResult
from chat.application.tools.web.services.web_search.utils.results import deduplicate_results_by_domain, is_valid_result


@dataclass(frozen=True, slots=True)
class TavilySearchRequest:
    """Tavily Search API 请求体。

    to_payload 对应 HTTP POST JSON Body。
    """

    query: str
    max_results: int = 10

    def to_payload(self) -> Dict[str, Any]:
        """转为 HTTP POST JSON Body。"""
        return {
            "query": self.query,
            "max_results": self.max_results,
            "auto_parameters": True,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }


def map_tavily_response(
        data: Mapping[str, Any],
        *,
        query: str,
        max_results: int,
) -> SearchResponse:

    raw_results = data.get("results") or []
    if not isinstance(raw_results, list):
        raw_results = []

    results: List[SearchResult] = []

    for item in raw_results:

        if not isinstance(item, Mapping):
            continue

        result = SearchResult(
            title=str(item.get("title") or "").strip(),
            url=str(item.get("url") or "").strip(),
            snippet=str(item.get("content") or item.get("snippet") or "").strip(),
        )

        if is_valid_result(result):
            results.append(result)

    results = deduplicate_results_by_domain(results, max_per_domain=2)

    return SearchResponse(
        query=query,
        results=results[:max_results],
        source=SearcherName.TAVILY,
    )