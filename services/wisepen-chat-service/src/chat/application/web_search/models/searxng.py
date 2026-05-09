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
from chat.application.web_search.utils import deduplicate_results_by_domain, deduplicate_images
from common.logger import log_fail

__all__ = [
    "SearXNGSearchRequest",
    "map_searxng_response",
    "merge_search_responses",
]


@dataclass(frozen=True, slots=True)
class SearXNGSearchRequest:
    query: str
    category: Optional[str] = None
    language: Optional[str] = None
    safesearch: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query 不能为空")

    def to_params(self) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "q": self.query,
            "format": "json",
        }

        if self.category:
            params["categories"] = self.category

        if self.language:
            params["language"] = self.language

        if self.safesearch is not None:
            params["safesearch"] = self.safesearch

        return params


def map_searxng_response(
    data: Mapping[str, Any],
    *,
    query: str,
    max_results: int,
    images_only: bool = False,
) -> SearchResponse:
    raw_results = data.get("results") or ()

    if not isinstance(raw_results, Sequence) or isinstance(raw_results, str):
        raw_results = ()

    raw_count = len(raw_results)

    if images_only:
        images = tuple(
            image
            for item in raw_results
            if isinstance(item, Mapping)
            for image in map_result_images(item)
            if image.url
        )
        images = deduplicate_images(images)

        return SearchResponse(
            query=query,
            results=(),
            answer=to_optional_str(data.get("answer")),
            images=images[:max_results],
        )

    mapped_results: List[SearchResult] = []

    for item in raw_results:
        if not isinstance(item, Mapping):
            continue

        result = map_searxng_result(item)
        if not is_valid_result(result):
            continue

        mapped_results.append(result)

    if raw_count > 0 and not mapped_results:
        log_fail(
            "SearXNG结果过滤",
            "原始结果全部被过滤",
            query=query,
            raw_count=raw_count,
        )

    results = deduplicate_results_by_domain(
        tuple(mapped_results),
        max_per_domain=2,
    )

    return SearchResponse(
        query=query,
        results=results[:max_results],
        answer=to_optional_str(data.get("answer")),
    )


def merge_search_responses(
    web_response: SearchResponse,
    image_response: SearchResponse,
) -> SearchResponse:
    return SearchResponse(
        query=web_response.query,
        results=web_response.results,
        answer=web_response.answer or image_response.answer,
        images=image_response.images,
        source=web_response.source or image_response.source,
    )


def map_searxng_result(item: Mapping[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        snippet=str(item.get("content") or item.get("snippet") or ""),
        images=map_result_images(item),
    )


def map_result_images(item: Mapping[str, Any]) -> Tuple[ImageResult, ...]:
    img_url = item.get("img_src")
    thumbnail_url = item.get("thumbnail_src") or item.get("thumbnail")
    source_url = item.get("url")

    if not img_url and not thumbnail_url:
        return ()

    return (
        ImageResult(
            url=str(img_url or thumbnail_url),
            desc=to_optional_str(item.get("title")),
            source_url=str(source_url) if source_url else None,
            thumbnail_url=str(thumbnail_url) if thumbnail_url else None,
            resolution=to_optional_str(item.get("resolution")),
        ),
    )