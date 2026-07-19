from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

import httpx
from ddgs import DDGS

from common.logger import warn

from .base import BaseProviderSearcher, SearchProviderConfig
from .core.errors import SearchProviderError
from .core.models import (
    ProviderSearchHttpRequest,
    ProviderSearchRequest,
    ProviderSearchResponse,
    ProviderSearchResult,
    SearchPreview,
)
from .core.protocols import ProviderSearcher
from ._utils import (
    as_dict_tuple,
    as_str,
    as_str_or_none,
    dedupe_by_url,
    has_search_result_fields,
)


@dataclass(frozen=True, slots=True)
class FourGetSearchRequest(ProviderSearchRequest):
    query: str
    max_results: int = 10

    def to_http_request(self) -> ProviderSearchHttpRequest:
        return ProviderSearchHttpRequest(
            method="GET",
            path="/api/v1/web",
            params={"s": self.query},
        )


class FourGetSearcher(BaseProviderSearcher):
    provider = None
    request_class = FourGetSearchRequest

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        config: SearchProviderConfig,
    ) -> None:
        super().__init__(
            http_client=http_client,
            config=config,
        )

    @staticmethod
    def map_response(
        data: dict[str, Any],
        *,
        query: str,
        max_results: int,
    ) -> ProviderSearchResponse:
        results = tuple(
            result
            for item in as_dict_tuple(data.get("web"))
            if (result := FourGetSearcher._map_result(item)) is not None
        )

        return ProviderSearchResponse(
            query=query,
            provider=None,
            results=dedupe_by_url(
                results,
                url_getter=lambda item: item.url,
                limit=max_results,
            ),
            answer=FourGetSearcher._map_answers(data.get("answer")),
        )

    @staticmethod
    def _map_result(
        item: dict[str, object],
    ) -> ProviderSearchResult | None:
        title = as_str(item.get("title"))
        url = as_str(item.get("url"))

        if not has_search_result_fields(title=title, url=url):
            return None

        return ProviderSearchResult(
            title=title,
            url=url,
            preview=SearchPreview(
                overview=as_str_or_none(item.get("description")),
            ),
        )

    @staticmethod
    def _map_answers(value: object) -> str | None:
        parts: list[str] = []

        for answer in as_dict_tuple(value):
            if title := as_str(answer.get("title")):
                parts.append(title)

            for node in as_dict_tuple(answer.get("description")):
                if text := as_str(node.get("value")):
                    parts.append(text)

        return "\n".join(parts).strip() or None


class DdgSearcher(ProviderSearcher):
    """无密钥本地备用搜索源。"""

    def __init__(
        self,
        *,
        proxy: str | None = None,
    ) -> None:
        self._proxy = proxy

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> ProviderSearchResponse:
        ddg = DDGS(proxy=self._proxy) if self._proxy else DDGS()

        items = await asyncio.to_thread(
            ddg.text,
            query,
            max_results=max_results,
        )

        return self.map_response(
            items,
            query=query,
            max_results=max_results,
        )

    @staticmethod
    def map_response(
        items: list[dict[str, Any]],
        *,
        query: str,
        max_results: int,
    ) -> ProviderSearchResponse:
        results = tuple(
            result
            for item in items
            if (result := DdgSearcher._map_result(item)) is not None
        )

        return ProviderSearchResponse(
            query=query,
            provider=None,
            results=dedupe_by_url(
                results,
                url_getter=lambda item: item.url,
                limit=max_results,
            ),
        )

    @staticmethod
    def _map_result(
        item: dict[str, Any],
    ) -> ProviderSearchResult | None:
        title = as_str(item.get("title"))
        url = as_str(item.get("href"))

        if not has_search_result_fields(title=title, url=url):
            return None

        return ProviderSearchResult(
            title=title,
            url=url,
            preview=SearchPreview(
                overview=as_str_or_none(item.get("body")),
            ),
        )


class PlatformDefaultSearcher(ProviderSearcher):
    """平台默认源：优先 4get，失败或无结果时降级至 DDGS。"""

    def __init__(
        self,
        *,
        fourget_searcher: FourGetSearcher,
        ddg_searcher: DdgSearcher,
    ) -> None:
        self._fourget = fourget_searcher
        self._ddg = ddg_searcher

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> ProviderSearchResponse:
        try:
            response = await self._fourget.search_web(
                query=query,
                max_results=max_results,
            )

            if response.results:
                return replace(response, provider=None)

        except SearchProviderError as exc:
            warn(
                "web search provider fallback.",
                from_provider="fourget",
                to_provider="ddgs",
                reason=exc.__class__.__name__,
            )

        response = await self._ddg.search_web(
            query=query,
            max_results=max_results,
        )

        return replace(response, provider=None)
