from __future__ import annotations

import asyncio

from ddgs import DDGS

from chat.application.tools.search_tools.web_search.providers.ddgs import map_ddgs_response
from chat.application.tools.search_tools.web_search.providers.models import ProviderSearchResponse
from chat.application.tools.search_tools.web_search.searchers.base import SearchProviderError


class DdgSearcher:
    """DDGS 本地搜索器（ddgs 库同步调用，用 asyncio.to_thread 包装）。

    作为 fourget 失败后的备用源，无需 API Key、无需 HTTP 服务。
    """

    def __init__(self, *, proxy: str | None = None) -> None:
        self._proxy = proxy or None

    async def search_web(
            self,
            *,
            query: str,
            max_results: int,
    ) -> ProviderSearchResponse:
        ddg = DDGS(proxy=self._proxy) if self._proxy else DDGS()
        items = await asyncio.to_thread(ddg.text, query, max_results=max_results)
        return map_ddgs_response(
            items,
            query=query,
            max_results=max_results,
        )

    async def search_academic(
            self,
            *,
            query: str,
            max_results: int,
    ) -> ProviderSearchResponse:
        raise SearchProviderError("DDGS does not support academic search.")
