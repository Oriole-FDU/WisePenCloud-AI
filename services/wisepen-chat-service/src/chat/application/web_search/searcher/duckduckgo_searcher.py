from __future__ import annotations

import asyncio
from typing import Any, Mapping, Tuple

from ddgs import DDGS

from chat.application.web_search.models import ImageResult, SearchResponse, SearchResult
from chat.application.web_search.models.common import is_valid_result
from chat.application.web_search.utils import deduplicate_results_by_domain, deduplicate_images

__all__ = [
    "DuckDuckGoBufferSearcher",
]


class DuckDuckGoBufferSearcher:
    name = "duckduckgo"

    def __init__(
        self,
        *,
        timeout: float = 8.0,
        region: str = "wt-wt",
        safesearch: str = "moderate",
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")

        if not region.strip():
            raise ValueError("region 不能为空")

        if not safesearch.strip():
            raise ValueError("safesearch 不能为空")

        self._timeout = timeout
        self._region = region
        self._safesearch = safesearch

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._search_sync,
                    query,
                    max_results,
                    with_images,
                ),
                timeout=self._timeout,
            )

        except asyncio.TimeoutError as e:
            raise RuntimeError(
                "DuckDuckGo timeout: "
                f"query={query!r}, max_results={max_results}, "
                f"with_images={with_images}, timeout={self._timeout}"
            ) from e

        except Exception as e:
            raise RuntimeError(
                "DuckDuckGo buffer search failed: "
                f"query={query!r}, max_results={max_results}, "
                f"with_images={with_images}, error={type(e).__name__}: {e}"
            ) from e

    def _search_sync(
        self,
        query: str,
        max_results: int,
        with_images: bool,
    ) -> SearchResponse:
        ddgs_timeout = max(1, int(self._timeout))

        with DDGS(timeout=ddgs_timeout) as ddgs:
            text_items = list(
                ddgs.text(
                    query,
                    region=self._region,
                    safesearch=self._safesearch,
                    max_results=max_results,
                )
            )

            results = tuple(
                result
                for item in text_items
                if isinstance(item, Mapping)
                for result in (map_text_result(item),)
                if is_valid_result(result)
            )
            results = deduplicate_results_by_domain(results, max_per_domain=2)

            images: Tuple[ImageResult, ...] = ()

            if with_images:
                image_items = list(
                    ddgs.images(
                        query,
                        region=self._region,
                        safesearch=self._safesearch,
                        max_results=max_results,
                    )
                )

                images = tuple(
                    image
                    for item in image_items
                    if isinstance(item, Mapping)
                    for image in (map_image_result(item),)
                    if image.url
                )
                images = deduplicate_images(images)

            return SearchResponse(
                query=query,
                results=results[:max_results],
                images=images[:max_results],
            )


def map_text_result(item: Mapping[str, Any]) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or ""),
        url=str(item.get("href") or item.get("url") or ""),
        snippet=str(item.get("body") or item.get("snippet") or ""),
    )


def map_image_result(item: Mapping[str, Any]) -> ImageResult:
    image_url = item.get("image") or item.get("img_src") or item.get("thumbnail")
    source_url = item.get("url") or item.get("source") or item.get("source_url")
    thumbnail_url = item.get("thumbnail") or item.get("thumbnail_url")

    return ImageResult(
        url=str(image_url or ""),
        desc=str(item.get("title") or "") or None,
        source_url=str(source_url) if source_url else None,
        thumbnail_url=str(thumbnail_url) if thumbnail_url else None,
    )