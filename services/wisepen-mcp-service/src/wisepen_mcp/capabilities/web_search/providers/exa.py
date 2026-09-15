from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from common.core.exceptions import ServiceException
from exa_py import AsyncExa

from wisepen_mcp.domain.error_codes import McpErrorCode

from ..search_tools import (
    BaseSearchTool,
    SearchRecency,
    SearchResponse,
    SearchResult,
)


class ExaSearchTool(BaseSearchTool):
    tool_name = "exa_search"
    provider_name = "exa"

    async def search_web(self, *, query: str, max_results: int, api_key: str | None, focus: str | None = None, recency: SearchRecency | None = None) -> SearchResponse:
        return await self._search(query=query, focus=focus, api_key=api_key, max_results=max_results, academic=False, recency=recency)

    async def search_academic(self, *, query: str, max_results: int, api_key: str | None, focus: str | None = None, recency: SearchRecency | None = None) -> SearchResponse:
        return await self._search(query=query, focus=focus, api_key=api_key, max_results=max_results, academic=True, recency=recency)

    async def _search(self, *, query: str, focus: str | None, max_results: int, api_key: str | None, academic: bool, recency: SearchRecency | None) -> SearchResponse:
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, "Exa API key is required.")

        search_options = {}
        if recency is not None:
            days = {
                SearchRecency.DAY: 1,
                SearchRecency.WEEK: 7,
                SearchRecency.MONTH: 30,
                SearchRecency.YEAR: 365,
            }[recency]
            # 近期意图转换为发表时间下限；crawl date 已失效，不能用于过滤。
            start = datetime.now(timezone.utc) - timedelta(days=days)
            search_options["start_published_date"] = start.isoformat()

        # Search 只请求高相关切片，避免把 Exa 全文抓取职责带入本能力。
        try:
            response = await AsyncExa(api_key=api_key).search(
                query,
                type="auto",
                num_results=max_results,
                category="research paper" if academic else None,
                contents={"highlights": {"query": focus} if focus else True, "summary": False, "text": False},
                **search_options,
            )
        except Exception as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"exa request failed: {exc}") from exc

        return self.map_response(response.results)

    @staticmethod
    def map_response(items: list[Any]) -> SearchResponse:
        return SearchResponse(
            results=[
                SearchResult(
                    title=item.title,
                    url=item.url,
                    published_date=item.published_date,
                    evidences=item.highlights or [],
                    metadata={
                        key: value
                        for key, value in {
                            "author": item.author,
                            "highlight_scores": item.highlight_scores,
                            "entities": item.entities,
                        }.items()
                        if value is not None
                    },
                )
                for item in items
            ]
        )
