from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

import httpx
from ddgs import DDGS

from common.logger import warn

from ..models import SearchResponse, SearchResult
from .base import (
    BaseProviderSearcher,
    ProviderSearchRequest,
    ProviderSearcher,
    SearchProviderConfig,
    SearchProviderError,
)
from .models import ProviderSearchHttpRequest
from ._utils import dedupe_results


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
    ) -> SearchResponse:
        return SearchResponse(
            query=query,
            provider=None,
            results=dedupe_results(
                (
                    SearchResult(
                        title=item.get("title"),
                        url=item.get("url"),
                        snippet=item.get("description"),
                    )
                    for item in data["web"]
                ),
                limit=max_results,
            ),
            answer=FourGetSearcher._map_answer(data["answer"]),
        )

    @staticmethod
    def _map_answer(answers: list[dict[str, object]]) -> str:
        return "\n".join(
            text
            for answer in answers
            for text in (
                answer["title"],
                *(node["value"] for node in answer["description"]),
            )
        )


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
    ) -> SearchResponse:
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
    ) -> SearchResponse:
        return SearchResponse(
            query=query,
            provider=None,
            results=dedupe_results(
                (
                    SearchResult(
                        title=item.get("title"),
                        url=item.get("href"),
                        snippet=item.get("body"),
                    )
                    for item in items
                ),
                limit=max_results,
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
    ) -> SearchResponse:
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
