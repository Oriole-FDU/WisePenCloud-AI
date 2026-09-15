from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from common.core.exceptions import ServiceException
from firecrawl import AsyncFirecrawl

from wisepen_mcp.domain.error_codes import McpErrorCode

from ..search_tools import (
    BaseSearchTool,
    SearchRecency,
    SearchResponse,
    SearchResult,
)


class FirecrawlSearchTool(BaseSearchTool):
    tool_name = "firecrawl_search"
    provider_name = "firecrawl"

    async def search_web(self, *, query: str, max_results: int, api_key: str | None, recency: SearchRecency | None = None) -> SearchResponse:
        return await self._search(query=query, api_key=api_key, max_results=max_results, academic=False, recency=recency)

    async def search_academic(self, *, query: str, max_results: int, api_key: str | None, recency: SearchRecency | None = None) -> SearchResponse:
        return await self._search(query=query, api_key=api_key, max_results=max_results, academic=True, recency=recency)


    async def _search(self, *, query: str, max_results: int, api_key: str | None, academic: bool, recency: SearchRecency | None) -> SearchResponse:
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, "Firecrawl API key is required.")

        client = AsyncFirecrawl(api_key=api_key)
        try:
            if academic:
                search_options = {}
                if recency is not None:
                    days = {
                        SearchRecency.DAY: 1,
                        SearchRecency.WEEK: 7,
                        SearchRecency.MONTH: 30,
                        SearchRecency.YEAR: 365,
                    }[recency]
                    today = datetime.now(timezone.utc).date()
                    # 学术接口包含首尾日期；N 个自然日的起点只能回溯 N - 1 天。
                    search_options = {
                        "from_date": (today - timedelta(days=days - 1)).isoformat(),
                        "to_date": today.isoformat(),
                    }
                data = await client.search_papers(query, k=max_results, **search_options)
            else:
                search_options = {}
                if recency is not None:
                    search_options["tbs"] = {
                        SearchRecency.DAY: "qdr:d",
                        SearchRecency.WEEK: "qdr:w",
                        SearchRecency.MONTH: "qdr:m",
                        SearchRecency.YEAR: "qdr:y",
                    }[recency]
                # Firecrawl v2 默认把相关段落放入 description，不附加 scrapeOptions。
                data = await client.v2.search(query, limit=max_results, sources=["web"], **search_options)
        except Exception as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"firecrawl request failed: {exc}") from exc

        return self.map_response(data, academic=academic)

    @staticmethod
    def map_response(data: dict[str, Any], *, academic: bool) -> SearchResponse:
        if academic:
            return SearchResponse(
                results=[
                    SearchResult(
                        title=item.get("title"),
                        url=item.get("url"),
                        evidences=[item["abstract"]] if item.get("abstract") else [],
                        metadata={"score": item["score"]} if item.get("score") is not None else {},
                    )
                    for item in data["results"]
                ]
            )
        return SearchResponse(
            results=[
                SearchResult(title=item.title, url=item.url, evidences=[item.description] if item.description else [])
                for item in data.web or []
            ]
        )
