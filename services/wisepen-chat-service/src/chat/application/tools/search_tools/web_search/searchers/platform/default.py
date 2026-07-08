from __future__ import annotations

from dataclasses import replace

from chat.application.tools.search_tools.web_search.providers.models import ProviderSearchResponse
from chat.application.tools.search_tools.web_search.searchers.base import SearchProviderError
from chat.application.tools.search_tools.web_search.searchers.platform.ddgs import DdgSearcher
from chat.application.tools.search_tools.web_search.searchers.platform.fourget import FourGetSearcher
from common.logger import warn


class PlatformDefaultSearcher:
    """4get + DDG 组合搜索器：4get 优先，失败或空结果后降级到 DDG。

    对外只暴露 platform_default，调用方无需感知 fourget/ddgs 的存在。
    """

    def __init__(self, *, fourget_searcher: FourGetSearcher, ddg_searcher: DdgSearcher) -> None:
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
                return replace(
                    response,
                    provider=None,
                    source_id="platform_default",
                )
        except SearchProviderError as exc:
            warn(
                "web search provider fallback.",
                from_provider="fourget",
                to_provider="ddgs",
                reason=exc.__class__.__name__,
                audit_message="4get 搜索失败，已降级到 DDGS 搜索。",
            )

        ddg_response = await self._ddg.search_web(
            query=query,
            max_results=max_results,
        )
        return replace(
            ddg_response,
            provider=None,
            source_id="platform_default",
        )

    async def search_academic(
            self,
            *,
            query: str,
            max_results: int,
    ) -> ProviderSearchResponse:
        raise SearchProviderError("platform_default does not support academic search.")
