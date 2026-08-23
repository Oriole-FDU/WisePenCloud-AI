from __future__ import annotations

from typing import Any

import httpx
from common.core.exceptions import ServiceException

from wisepen_mcp.core.config.app_settings import settings
from wisepen_mcp.domain.error_codes import McpErrorCode

from ..search_tools import (
    BaseSearchTool,
    SearchResponse,
    SearchResult,
)


class ExaSearchTool(BaseSearchTool):
    tool_name = "exa_search"
    provider_name = "exa"

    def __init__(self, *, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client


    async def search_web(self, *, query: str, max_results: int, api_key: str | None) -> SearchResponse:
        return await self._search(query=query, api_key=api_key, max_results=max_results, academic=False)

    async def search_academic(self, *, query: str, max_results: int, api_key: str | None) -> SearchResponse:
        return await self._search(query=query, api_key=api_key, max_results=max_results, academic=True)

    async def _search(self, *, query: str, max_results: int, api_key: str | None, academic: bool) -> SearchResponse:
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, "Exa API key is required.")

        payload: dict[str, object] = {
            "query": query,
            "type": "auto",
            "numResults": max_results,
            "contents": {"highlights": True, "summary": True, "text": False},
        }
        if academic:
            payload["category"] = "research paper"

        try:
            response = await self._http_client.post(f"{settings.WEB_SEARCH_EXA_BASE_URL.rstrip('/')}/search", headers={"x-api-key": api_key}, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403, 429}:
                raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, f"exa credential unavailable or quota exhausted: HTTP {status_code}") from exc
            if status_code >= 500:
                raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"exa remote service unavailable: HTTP {status_code}") from exc
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, f"exa request failed: HTTP {status_code}") from exc
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"exa network request failed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, f"exa request failed: {exc}") from exc

        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "exa response is not a JSON object.")
            return self.map_response(data)
        except ValueError as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "exa response is not valid JSON.") from exc
        except (KeyError, TypeError, AttributeError) as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "exa response JSON shape is invalid.") from exc

    @staticmethod
    def map_response(data: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            results=[
                SearchResult(
                    title=item.get("title"),
                    url=item.get("url"),
                    snippet=item.get("summary"),
                    highlights=highlights if (highlights := item.get("highlights")) is not None else None,
                )
                for item in data["results"]
            ]
        )
