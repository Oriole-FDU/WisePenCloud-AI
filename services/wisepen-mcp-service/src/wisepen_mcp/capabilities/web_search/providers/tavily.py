from __future__ import annotations

from typing import Any

from common.core.exceptions import ServiceException
from tavily import AsyncTavilyClient

from wisepen_mcp.domain.error_codes import McpErrorCode

from ..search_tools import (
    BaseSearchTool,
    SearchResponse,
    SearchResult,
)


class TavilySearchTool(BaseSearchTool):
    tool_name = "tavily_search"
    provider_name = "tavily"

    async def search_web(self, *, query: str, focus: str | None, max_results: int, api_key: str | None) -> SearchResponse:
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, "Tavily API key is required.")

        # 固定基础档与切片数，Search 不请求全文正文。
        try:
            data = await AsyncTavilyClient(api_key=api_key).search(
                f"{query}\n{focus}" if focus else query,
                search_depth="basic",
                max_results=max_results,
                include_answer="basic",
                include_raw_content=False,
                include_images=False,
                chunks_per_source=3,
            )
        except Exception as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"tavily request failed: {exc}") from exc

        return self.map_response(data)

    @staticmethod
    def map_response(data: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            results=[
                SearchResult(title=item.get("title"), url=item.get("url"), evidences=[item["content"]] if item.get("content") else [])
                for item in data["results"]
            ],
            summary=data["answer"],
        )
