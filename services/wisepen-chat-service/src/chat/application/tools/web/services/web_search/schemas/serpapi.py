from dataclasses import dataclass
from typing import Any, Dict, Mapping, List

from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.models import SearchResponse, SearchResult
from chat.application.tools.web.services.web_search.utils.results import deduplicate_results_by_domain, is_valid_result


@dataclass(frozen=True, slots=True)
class SerpApiSearchRequest:
    query: str
    api_key: str
    num: int

    def to_params(self) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "engine": "google",
            "q": self.query,
            "api_key": self.api_key,
            "num": str(self.num),
        }
        return params


def map_serpapi_response(
        data: Mapping[str, Any],
        *,
        query: str,
        max_results: int,
) -> SearchResponse:

    raw_items = data.get("organic_results") or []
    if not isinstance(raw_items, list):
        raw_items = []

    results: List[SearchResult] = []

    for item in raw_items:

        if not isinstance(item, Mapping):
            continue

        result = SearchResult(
            title=str(item.get("title") or "").strip(),
            url=str(item.get("link") or "").strip(),
            snippet=str(item.get("snippet") or "").strip(),
        )

        if is_valid_result(result):
            results.append(result)

    results = deduplicate_results_by_domain(results, max_per_domain=2)

    return SearchResponse(
        query=query,
        results=results[:max_results],
        source=SearcherName.SERPAPI,
    )