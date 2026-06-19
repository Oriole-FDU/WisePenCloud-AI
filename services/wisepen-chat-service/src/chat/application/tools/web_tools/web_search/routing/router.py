from __future__ import annotations

import json

from async_lru import alru_cache

from chat.application.utils.llm_clients import LiteLLMQueryClient, build_query_client
from common.logger import info
from ..prompts import ROUTE_CLASSIFICATION_SYSTEM_PROMPT
from .endpoint_planner import SearchIntentRoute


_ROUTE_MAP: dict[str, SearchIntentRoute] = {r.value: r for r in SearchIntentRoute}


class WebSearchRouter:
    """使用小模型把搜索 query 分类到搜索路由。"""

    def __init__(
        self,
        *,
        client: LiteLLMQueryClient | None = None,
    ) -> None:
        self._client = client or build_query_client()

    @alru_cache(maxsize=1024)
    async def route(self, query: str) -> SearchIntentRoute:
        result = await self._client.aquery(
            prompt=f"<query>{query.strip()}</query>",
            system_prompt=ROUTE_CLASSIFICATION_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=32,  # {"route": "academic"} 约 10 token，留足余量
        )
        info("web_search.route_classification", query=query.strip()[:80], raw_response=result.content)

        try:
            value = str(json.loads(result.content.strip()).get("route", "")).lower().strip()
        except (json.JSONDecodeError, AttributeError):
            return SearchIntentRoute.GENERAL

        return _ROUTE_MAP.get(value, SearchIntentRoute.GENERAL)
