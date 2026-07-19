from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import ProviderSearchResponse


@runtime_checkable
class ProviderSearcher(Protocol):
    """可由 Web Search 编排层调用的搜索源。

    默认 academic 搜索复用 web；只有具备原生学术接口的搜索源需要覆盖它。
    """

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> ProviderSearchResponse: ...

    async def search_academic(
        self,
        *,
        query: str,
        max_results: int,
    ) -> ProviderSearchResponse:
        return await self.search_web(
            query=query,
            max_results=max_results,
        )
