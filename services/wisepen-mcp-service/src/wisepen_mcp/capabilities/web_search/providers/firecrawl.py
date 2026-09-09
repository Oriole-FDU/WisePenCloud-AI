from __future__ import annotations

from typing import Any

from common.core.exceptions import ServiceException
from firecrawl import AsyncFirecrawl

from wisepen_mcp.domain.error_codes import McpErrorCode

from ..search_tools import (
    BaseSearchTool,
    SearchResponse,
    SearchResult,
)


class FirecrawlSearchTool(BaseSearchTool):
    tool_name = "firecrawl_search"
    provider_name = "firecrawl"

    async def search_web(self, *, query: str, focus: str | None, max_results: int, api_key: str | None) -> SearchResponse:
        return await self._search(query=query, api_key=api_key, max_results=max_results, academic=False)

    async def search_academic(self, *, query: str, focus: str | None, max_results: int, api_key: str | None) -> SearchResponse:
        return await self._search(query=query, api_key=api_key, max_results=max_results, academic=True)


    async def _search(self, *, query: str, max_results: int, api_key: str | None, academic: bool) -> SearchResponse:
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, "Firecrawl API key is required.")

        client = AsyncFirecrawl(api_key=api_key)
        try:
            if academic:
                data = await client.search_papers(query, k=max_results)
            else:
                # Firecrawl v2 默认把相关段落放入 description，不附加 scrapeOptions。
                data = await client.v2.search(query, limit=max_results, sources=["web"])
        except Exception as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"firecrawl request failed: {exc}") from exc

        return self.map_response(data, academic=academic)

    @staticmethod
    def map_response(data: dict[str, Any], *, academic: bool) -> SearchResponse:
        if academic:
            return SearchResponse(results=[SearchResult(title=item.get("title"), url=item.get("url"), evidences=[item["abstract"]] if item.get("abstract") else [], metadata={key: item[key] for key in ("paperId", "primaryId", "sourceIds", "score") if item.get(key) is not None}) for item in data["results"]])
        return SearchResponse(
            results=[
                SearchResult(title=item.title, url=item.url, evidences=[item.description] if item.description else [])
                for item in data.web or []
            ]
        )
