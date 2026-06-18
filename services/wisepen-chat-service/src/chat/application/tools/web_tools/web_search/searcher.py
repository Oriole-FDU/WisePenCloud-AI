from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping

from .providers.models import ProviderSearchResponse, SearchProviderName
from .routing.endpoint_planner import SearchEndpointPlan
from .searchers import ProviderSearcher, SearchProviderError


class WebSearchProviderSearcher:
    """按 endpoint plan 调度已构造好的 provider searcher。"""

    def __init__(self, *, provider_searchers: Mapping[SearchProviderName, ProviderSearcher]) -> None:
        self._searchers = dict(provider_searchers)

    async def search(
        self,
        *,
        query: str,
        plan: SearchEndpointPlan,
        max_results: int,
    ) -> ProviderSearchResponse:
        try:
            searcher = self._searchers[plan.provider]
        except KeyError as exc:
            raise SearchProviderError(f"缺少 provider searcher: {plan.provider}") from exc

        return await searcher.search(
            query=query,
            endpoint=plan.endpoint,
            max_results=max_results,
        )

    async def search_many(
        self,
        *,
        query: str,
        plans: Iterable[SearchEndpointPlan],
        max_results: int,
    ) -> list[ProviderSearchResponse]:
        tasks = [
            self.search(query=query, plan=plan, max_results=max_results)
            for plan in plans
        ]
        return list(await asyncio.gather(*tasks))


