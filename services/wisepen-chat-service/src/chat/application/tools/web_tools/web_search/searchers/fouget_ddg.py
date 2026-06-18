from __future__ import annotations

from dataclasses import replace

from common.logger import warn

from ..providers.models import ProviderSearchResponse, SearchProviderEndpoint, SearchProviderName
from .base import SearchProviderError
from .ddgs import DdgSearcher
from .fourget import FourGetSearcher


class FouGetDdgSearcher:
    """4get + DDG 组合搜索器：4get 优先，失败或空结果后降级到 DDG。

    对外暴露为单一 provider FOUGET_DDG，内部自动处理降级，
    调用方无需感知 fourget/ddg 的存在。
    """

    def __init__(self, *, fourget_searcher: FourGetSearcher, ddg_searcher: DdgSearcher) -> None:
        self._fourget = fourget_searcher
        self._ddg = ddg_searcher

    async def search(
        self,
        *,
        query: str,
        endpoint: SearchProviderEndpoint,
        max_results: int,
    ) -> ProviderSearchResponse:
        # 1. 优先 fourget
        try:
            response = await self._fourget.search(
                query=query,
                endpoint=endpoint,
                max_results=max_results,
            )
            if response.results:
                return response
            # fourget 返回空结果，降级到 ddg
        except SearchProviderError as exc:
            # fourget 请求失败（网络/凭证/解析），降级到 ddg
            warn(
                "web search provider fallback.",
                from_provider="fourget",
                to_provider="ddg",
                endpoint=endpoint,
                reason=exc.__class__.__name__,
                audit_message="4get 搜索失败，已降级到 DDG 搜索。",
            )

        # 2. 降级到 ddg，响应统一标记为 FOUGET_DDG
        ddg_response = await self._ddg.search(
            query=query,
            endpoint=endpoint,
            max_results=max_results,
        )
        return ddg_response
