from __future__ import annotations

from typing import Any

from common.core.exceptions import ServiceException
from exa_py import AsyncExa

from wisepen_mcp.domain.error_codes import McpErrorCode

from ..search_tools import (
    BaseSearchTool,
    SearchResponse,
    SearchResult,
)


class ExaSearchTool(BaseSearchTool):
    tool_name = "exa_search"
    provider_name = "exa"

    async def search_web(self, *, query: str, focus: str | None, max_results: int, api_key: str | None) -> SearchResponse:
        return await self._search(query=query, focus=focus, api_key=api_key, max_results=max_results, academic=False)

    async def search_academic(self, *, query: str, focus: str | None, max_results: int, api_key: str | None) -> SearchResponse:
        return await self._search(query=query, focus=focus, api_key=api_key, max_results=max_results, academic=True)

    async def _search(self, *, query: str, focus: str | None, max_results: int, api_key: str | None, academic: bool) -> SearchResponse:
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, "Exa API key is required.")

        # Search 只请求高相关切片，避免把 Exa 全文抓取职责带入本能力。
        try:
            response = await AsyncExa(api_key=api_key).search(
                query,
                type="auto",
                num_results=max_results,
                category="research paper" if academic else None,
                contents={"highlights": {"query": focus} if focus else True, "summary": False, "text": False},
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
                            "id": item.id,
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
