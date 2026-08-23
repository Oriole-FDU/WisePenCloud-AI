from __future__ import annotations

import asyncio

import httpx
from common.core.exceptions import ServiceException
from common.logger import warn
from common.utils.ranking import RankingPipeline
from ddgs import DDGS

from wisepen_mcp.core.config.app_settings import settings
from wisepen_mcp.domain.error_codes import McpErrorCode

from ..search_tools import (
    BaseSearchTool,
    SearchResponse,
    SearchResult,
)


class PlatformSearchTool(BaseSearchTool):
    """平台默认搜索工具，优先 4get，失败或无结果时降级至 DDGS"""

    tool_name = "default_web_search"
    provider_name = None
    requires_api_key = False

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        ranking_pipeline: RankingPipeline,
        ddg_proxy: str | None = None,
    ) -> None:
        super().__init__(ranking_pipeline=ranking_pipeline)
        self._http_client = http_client
        self._ddg_proxy = ddg_proxy

    async def search_web(self, *, query: str, max_results: int, api_key: str | None) -> SearchResponse:
        try:
            response = await self._search_fourget(query=query, max_results=max_results)
            if response.results:
                return SearchResponse(results=response.results, answer=response.answer)

        except ServiceException as exc:
            warn("web search provider fallback.", from_provider="fourget", to_provider="ddgs", reason=exc.msg,)

        response = await self._search_ddg(query=query, max_results=max_results)

        return SearchResponse(results=response.results, answer=response.answer)

    async def _search_fourget(
        self,
        *,
        query: str,
        max_results: int,
    ) -> SearchResponse:
        try:
            response = await self._http_client.get(f"{settings.WEB_SEARCH_FOURGET_BASE_URL.rstrip('/')}/api/v1/web", params={"s": query})
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code >= 500:
                raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"fourget remote service unavailable: HTTP {status_code}") from exc
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, f"fourget request failed: HTTP {status_code}") from exc
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"fourget network request failed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, f"fourget request failed: {exc}") from exc

        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "fourget response is not a JSON object.")
        except ValueError as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "fourget response is not valid JSON.") from exc

        answer_lines: list[str] = []
        for answer in data["answer"]:
            answer_lines.append(str(answer["title"]))
            for node in answer["description"]: answer_lines.append(str(node["value"]))

        try:
            return SearchResponse(
                results=[
                    SearchResult(title=item.get("title"), url=item.get("url"), snippet=item.get("description"))
                    for item in data["web"]
                ],
                answer="\n".join(answer_lines)
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "fourget response JSON shape is invalid.") from exc

    async def _search_ddg(
        self,
        *,
        query: str,
        max_results: int,
    ) -> SearchResponse:
        ddg = DDGS(proxy=self._ddg_proxy) if self._ddg_proxy else DDGS()

        try:
            items = await asyncio.to_thread(ddg.text, query, max_results=max_results)
        except Exception as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"ddgs request failed: {exc}") from exc

        try:
            return SearchResponse(
                results=[
                    SearchResult(title=item.get("title"), url=item.get("href"), snippet=item.get("body"))
                    for item in items
                ]
            )
        except (TypeError, AttributeError) as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "ddgs response shape is invalid.") from exc
