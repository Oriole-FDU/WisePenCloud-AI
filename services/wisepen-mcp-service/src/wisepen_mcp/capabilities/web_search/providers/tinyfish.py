from __future__ import annotations

from typing import Any

import httpx
from common.core.exceptions import ServiceException
from common.utils.ranking import RankingPipeline

from wisepen_mcp.core.config.app_settings import settings
from wisepen_mcp.domain.error_codes import McpErrorCode

from ..search_tools import (
    BaseSearchTool,
    SearchResponse,
    SearchResult,
)


class TinyFishSearchTool(BaseSearchTool):
    tool_name = "tinyfish_search"
    provider_name = "tinyfish"

    def __init__(self, *, http_client: httpx.AsyncClient, ranking_pipeline: RankingPipeline) -> None:
        super().__init__(ranking_pipeline=ranking_pipeline)
        self._http_client = http_client

    async def search_web(self, *, query: str, max_results: int, api_key: str | None) -> SearchResponse:
        return await self._search(query=query, api_key=api_key, academic=False)

    async def search_academic(self, *, query: str, max_results: int, api_key: str | None) -> SearchResponse:
        return await self._search(query=query, api_key=api_key, academic=True)

    async def _search(self, *, query: str, api_key: str | None, academic: bool) -> SearchResponse:
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, "TinyFish API key is required.")

        params: dict[str, object] = {"query": query}
        if academic:
            params["domain_type"] = "research_paper"

        try:
            response = await self._http_client.get(settings.WEB_SEARCH_TINYFISH_BASE_URL.rstrip("/"), headers={"X-API-Key": api_key}, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403, 429}:
                raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, f"tinyfish credential unavailable or quota exhausted: HTTP {status_code}") from exc
            if status_code >= 500:
                raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"tinyfish remote service unavailable: HTTP {status_code}") from exc
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, f"tinyfish request failed: HTTP {status_code}") from exc
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"tinyfish network request failed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, f"tinyfish request failed: {exc}") from exc

        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "tinyfish response is not a JSON object.")
            return self.map_response(data)
        except ValueError as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "tinyfish response is not valid JSON.") from exc
        except (KeyError, TypeError, AttributeError) as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "tinyfish response JSON shape is invalid.") from exc

    @staticmethod
    def map_response(data: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            results=[
                SearchResult(title=item.get("title"), url=item.get("url"), snippet=item.get("snippet"))
                for item in data["results"]
            ],
        )
