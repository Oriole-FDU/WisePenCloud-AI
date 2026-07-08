from __future__ import annotations

from chat.application.tools.search_tools.web_search.core.sources import WebSearchRuntimeSource
from chat.application.tools.search_tools.web_search.pipeline.search_executor import (
    WebSearchResult,
    execute_provider_search,
)
from chat.application.tools.search_tools.web_search.providers.models import SearchMode


class SearchService:
    """Search 编排服务。

    service 不读取用户配置、不解密凭证；搜索源必须先固化到 source 对象。
    """

    async def search(
            self,
            *,
            query: str,
            max_results: int = 10,
            source: WebSearchRuntimeSource,
            mode: SearchMode = SearchMode.WEB,
    ) -> WebSearchResult:
        if mode == SearchMode.ACADEMIC:
            search_once = lambda searcher: searcher.search_academic(query=query, max_results=max_results)
        else:
            search_once = lambda searcher: searcher.search_web(query=query, max_results=max_results)
        return await execute_provider_search(
            query=query,
            source=source,
            search_once=search_once,
        )
