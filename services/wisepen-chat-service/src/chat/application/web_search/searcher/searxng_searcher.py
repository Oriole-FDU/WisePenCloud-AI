from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import httpx

from chat.application.web_search.models import SearchResponse
from chat.application.web_search.models.searxng import (
    SearXNGSearchRequest,
    map_searxng_response,
    merge_search_responses,
)
from common.logger import log_fail

__all__ = [
    "SearXNGSearcher",
]


class SearXNGSearcher:
    name = "searxng"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
        language: Optional[str] = None,
        safesearch: Optional[int] = None,
        web_category: str = "general",
        image_category: str = "images",
    ) -> None:
        base_url = base_url.rstrip("/")

        if not base_url:
            raise ValueError("base_url 不能为空")

        if not web_category.strip():
            raise ValueError("web_category 不能为空")

        if not image_category.strip():
            raise ValueError("image_category 不能为空")

        self._base_url = base_url
        self._timeout = timeout
        self._language = language.strip() or None if language else None
        self._safesearch = safesearch
        self._web_category = web_category
        self._image_category = image_category

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        if not with_images:
            return await self._search_web(
                query=query,
                max_results=max_results,
            )

        web_result, image_result = await asyncio.gather(
            self._search_web(query=query, max_results=max_results),
            self._search_images(query=query, max_results=max_results),
            return_exceptions=True,
        )

        if isinstance(web_result, Exception):
            raise web_result

        if isinstance(image_result, Exception):
            log_fail(
                "SearXNG图片搜索失败",
                image_result,
                query=query,
                max_results=max_results,
            )
            return web_result

        return merge_search_responses(web_result, image_result)

    async def _search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> SearchResponse:
        request = SearXNGSearchRequest(
            query=query,
            category=self._web_category,
            language=self._language,
            safesearch=self._safesearch,
        )

        data = await self._get_json(request.to_params())

        return map_searxng_response(
            data,
            query=query,
            max_results=max_results,
            images_only=False,
        )

    async def _search_images(
        self,
        *,
        query: str,
        max_results: int,
    ) -> SearchResponse:
        request = SearXNGSearchRequest(
            query=query,
            category=self._image_category,
            language=self._language,
            safesearch=self._safesearch,
        )

        data = await self._get_json(request.to_params())

        return map_searxng_response(
            data,
            query=query,
            max_results=max_results,
            images_only=True,
        )

    async def _get_json(self, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url}/search"
        response: Optional[httpx.Response] = None

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

        except httpx.HTTPStatusError as e:
            body = e.response.text[:500]
            raise RuntimeError(
                "SearXNG HTTP error: "
                f"status={e.response.status_code}, "
                f"url={e.request.url}, "
                f"params={params}, "
                f"body={body!r}"
            ) from e

        except httpx.TimeoutException as e:
            raise RuntimeError(
                "SearXNG timeout: "
                f"url={url}, params={params}, timeout={self._timeout}"
            ) from e

        except httpx.RequestError as e:
            raise RuntimeError(
                "SearXNG request error: "
                f"url={url}, params={params}"
            ) from e

        except ValueError as e:
            raw = response.text[:500] if response is not None else ""
            raise RuntimeError(
                "SearXNG invalid JSON: "
                f"url={url}, params={params}, body={raw!r}"
            ) from e

        if not isinstance(data, dict):
            raise RuntimeError(
                f"SearXNG invalid response type: type={type(data).__name__}, params={params}"
            )

        return data