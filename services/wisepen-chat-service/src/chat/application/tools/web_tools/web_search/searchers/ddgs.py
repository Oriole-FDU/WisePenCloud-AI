from __future__ import annotations

import asyncio

from ddgs import DDGS

from ..providers.ddgs import map_ddgs_response
from ..providers.models import ProviderSearchResponse, SearchProviderEndpoint


class DdgSearcher:
    """DuckDuckGo 本地搜索器（ddgs 库同步调用，用 asyncio.to_thread 包装）。

    作为 fourget 失败后的备用源，无需 API Key、无需 HTTP 服务。
    """

    def __init__(self, *, proxy: str | None = None) -> None:
        self._proxy = proxy or None

    async def search(
        self,
        *,
        query: str,
        endpoint: SearchProviderEndpoint,
        max_results: int,
    ) -> ProviderSearchResponse:
        ddg = DDGS(proxy=self._proxy) if self._proxy else DDGS()
        if endpoint == SearchProviderEndpoint.NEWS:
            items = await asyncio.to_thread(ddg.news, query, max_results=max_results)
        else:
            items = await asyncio.to_thread(ddg.text, query, max_results=max_results)
        return map_ddgs_response(
            items,
            query=query,
            endpoint=endpoint,
            max_results=max_results,
        )
