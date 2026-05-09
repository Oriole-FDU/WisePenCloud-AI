from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from chat.application.web_search.models.common import (
    ImageResult,
    SearchResponse,
    SearchResult,
    is_valid_result,
    to_optional_str,
)
from chat.application.web_search.utils import (
    deduplicate_results_by_domain,
    deduplicate_images,
)
__all__ = [
    "TavilySearchRequest",
    "map_tavily_response",
]


@dataclass(frozen=True, slots=True)
class TavilySearchRequest:
    """Tavily Search API 请求体。

    只接收公共搜索语义，然后在 to_payload() 中映射为 Tavily 参数。
    """

    query: str
    max_results: int = 5
    with_images: bool = False

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query 不能为空")

        if not 1 <= self.max_results <= 20:
            raise ValueError("max_results 必须在 1 到 20 之间")

    def to_payload(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "max_results": self.max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": self.with_images,
            "search_depth": "basic",
        }


def map_tavily_response(data: Mapping[str, Any]) -> SearchResponse:
    """将 Tavily 原始响应映射为通用 SearchResponse"""

    raw_results = data.get("results") or ()

    if not isinstance(raw_results, Sequence) or isinstance(raw_results, str):
        raw_results = ()

    results = tuple(
        result
        for item in raw_results
        if isinstance(item, Mapping)
        for result in (map_tavily_result(item),)
        if is_valid_result(result)
    )
    results = deduplicate_results_by_domain(results, max_per_domain=2)

    return SearchResponse(
        query=str(data.get("query") or ""),
        results=results,
        answer=to_optional_str(data.get("answer")),
        images=map_images(data.get("images"))[:5],
    )


def map_tavily_result(item: Mapping[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        snippet=str(item.get("content") or item.get("snippet") or ""),
        images=map_images(item.get("images")),
    )


def map_images(items: Any) -> Tuple[ImageResult, ...]:
    if not isinstance(items, Sequence) or isinstance(items, str):
        return ()

    images: List[ImageResult] = []

    for item in items:
        image = map_image(item)
        if image is not None:
            images.append(image)

    return deduplicate_images(images)


def map_image(item: Any) -> Optional[ImageResult]:
    if isinstance(item, str):
        return ImageResult(url=item)

    if not isinstance(item, Mapping):
        return None

    url = item.get("url")
    if not url:
        return None

    desc = item.get("description") or item.get("desc") or item.get("alt")
    source_url = item.get("source_url") or item.get("source") or item.get("page_url")
    thumbnail_url = (
        item.get("thumbnail_url")
        or item.get("thumbnail")
        or item.get("thumbnail_src")
    )

    return ImageResult(
        url=str(url),
        desc=str(desc) if desc is not None else None,
        source_url=str(source_url) if source_url else None,
        thumbnail_url=str(thumbnail_url) if thumbnail_url else None,
    )