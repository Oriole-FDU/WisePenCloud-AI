from __future__ import annotations

from typing import Any

from common.core.exceptions import ServiceException
from tavily import AsyncTavilyClient

from wisepen_mcp.domain.error_codes import McpErrorCode

from ..model import (
    SearchRecency,
    SearchResponse,
    SearchResult,
)
from ..search_tools import (
    BaseSearchTool,
    ToolMeta,
    ToolUISpecMeta,
    _WEB_SEARCH_OUTPUT_CACHE_SPEC,
    _WEB_SEARCH_POLICY_META,
    _WEB_SEARCH_REQUIRED_API_KEY_CONFIG,
)


class TavilySearchTool(BaseSearchTool):
    tool_name = "tavily_search"
    tool_meta = ToolMeta(
        tool_name="tavily_search",
        policy=_WEB_SEARCH_POLICY_META,
        ui_spec=ToolUISpecMeta(display_name="Tavily 搜索"),
        config_spec=_WEB_SEARCH_REQUIRED_API_KEY_CONFIG,
        output_cache_spec=_WEB_SEARCH_OUTPUT_CACHE_SPEC,
        failure_reason="Tavily Search Failed",
    )
    provider_name = "tavily"

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
        api_key: str | None,
        recency: SearchRecency | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> SearchResponse:
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, "Tavily API key is required.")

        search_options = {"time_range": recency.value} if recency is not None else {}
        if include_domains:
            search_options["include_domains"] = include_domains
        if exclude_domains:
            search_options["exclude_domains"] = exclude_domains

        try:
            data = await AsyncTavilyClient(api_key=api_key).search(
                query,
                search_depth="basic",
                max_results=max_results,
                include_answer="basic",
                include_raw_content=False,
                include_images=False,
                chunks_per_source=3,
                **search_options,
            )
        except Exception as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"tavily request failed: {exc}") from exc

        return self.map_response(data)

    @staticmethod
    def map_response(data: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            results=[
                SearchResult(
                    title=item.get("title"),
                    url=item.get("url"),
                    evidences=[item["content"]] if item.get("content") else [])
                for item in data["results"]
            ],
            summary=data["answer"],
        )
