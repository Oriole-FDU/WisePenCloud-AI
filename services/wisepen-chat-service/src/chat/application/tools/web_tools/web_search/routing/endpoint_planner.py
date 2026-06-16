from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from ..providers.models import SearchProviderEndpoint, SearchProviderName


class SearchIntentRoute(StrEnum):
    GENERAL = "general"
    NEWS = "news"
    ACADEMIC = "academic"


@dataclass(frozen=True, slots=True)
class SearchEndpointPlan:
    provider: SearchProviderName
    endpoint: SearchProviderEndpoint
    route: SearchIntentRoute  # 实际匹配到的 route，回落时为 GENERAL


# provider 支持的 route → endpoint；未列出的 route 在查询时回落到 GENERAL
_PROVIDER_ROUTES: dict[SearchProviderName, dict[SearchIntentRoute, SearchProviderEndpoint]] = {
    SearchProviderName.FOURGET: {
        SearchIntentRoute.GENERAL: SearchProviderEndpoint.WEB,
        SearchIntentRoute.NEWS:    SearchProviderEndpoint.NEWS,
    },
    SearchProviderName.EXA: {
        SearchIntentRoute.GENERAL:  SearchProviderEndpoint.WEB,
        SearchIntentRoute.NEWS:     SearchProviderEndpoint.NEWS,
        SearchIntentRoute.ACADEMIC: SearchProviderEndpoint.SCHOLAR,
    },
    SearchProviderName.TAVILY: {
        SearchIntentRoute.GENERAL: SearchProviderEndpoint.WEB,
        SearchIntentRoute.NEWS:    SearchProviderEndpoint.NEWS,
    },
    SearchProviderName.ANYSEARCH: {
        SearchIntentRoute.GENERAL: SearchProviderEndpoint.WEB,
    },
}


def resolve_endpoint_plans(
    route: SearchIntentRoute,
    providers: Iterable[SearchProviderName],
) -> list[SearchEndpointPlan]:
    """为每个 provider 选出最匹配 route 的执行计划，无精确匹配时回落到 GENERAL。"""
    plans: list[SearchEndpointPlan] = []
    for provider in providers:
        route_map = _PROVIDER_ROUTES.get(provider, {})
        for candidate in (route, SearchIntentRoute.GENERAL):
            if candidate in route_map:
                plans.append(SearchEndpointPlan(
                    provider=provider,
                    endpoint=route_map[candidate],
                    route=candidate,
                ))
                break
    return plans
