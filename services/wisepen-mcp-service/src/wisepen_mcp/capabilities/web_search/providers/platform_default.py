from __future__ import annotations

from typing import Any

import httpx
from common.core.exceptions import ServiceException

from wisepen_mcp.core.config.app_settings import settings
from wisepen_mcp.domain.error_codes import McpErrorCode

from ..search_tools import BaseSearchTool, SearchRecency, SearchResponse, SearchResult


class PlatformSearchTool(BaseSearchTool):
    """平台托管的默认搜索，通过 GLM 基础搜索返回候选网页的摘要证据。"""

    tool_name = "default_web_search"
    provider_name = None
    requires_api_key = False  # 不要求调用方提供 BYOK；托管密钥在 Provider 请求前检查。

    def __init__(self, *, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client

    async def search_web(self, *, query: str, max_results: int, api_key: str | None, recency: SearchRecency | None = None) -> SearchResponse:
        # 默认工具只使用平台托管凭证，请求侧 api_key 不得覆盖平台配置。
        api_key = settings.WEB_SEARCH_API_KEY.strip()
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CONFIG_MISSING, "Platform Web Search API key is not configured.")

        url = f"{settings.WEB_SEARCH_GLM_BASE_URL.rstrip('/')}/web_search"
        payload = {
            # GLM 的 70 字符限制只在 Adapter 边界裁剪，不收紧其他 Provider 的公共输入。
            "search_query": query[:70],
            "search_engine": "search_std",
            "search_intent": False,
            "count": max_results,
            "content_size": "medium",
        }
        if recency is not None:
            payload["search_recency_filter"] = {
                SearchRecency.DAY: "oneDay",
                SearchRecency.WEEK: "oneWeek",
                SearchRecency.MONTH: "oneMonth",
                SearchRecency.YEAR: "oneYear",
            }[recency]

        try:
            response = await self._http_client.post(url, headers={"Authorization": f"Bearer {api_key}"}, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403, 429}:
                raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, f"glm credential unavailable or quota exhausted: HTTP {status_code}") from exc
            if status_code >= 500:
                raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"glm remote service unavailable: HTTP {status_code}") from exc
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, f"glm request failed: HTTP {status_code}") from exc
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, "glm network request failed.") from exc
        except httpx.HTTPError as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "glm request failed.") from exc

        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "glm response is not a JSON object.")
            return self.map_response(data)
        except ValueError as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "glm response is not valid JSON.") from exc
        except (KeyError, TypeError, AttributeError) as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "glm response JSON shape is invalid.") from exc

    @staticmethod
    def map_response(data: dict[str, Any]) -> SearchResponse:
        results: list[SearchResult] = []
        for item in data["search_result"]:
            content = (item.get("content") or "").strip()
            published_date = (item.get("publish_date") or "").strip() or None
            media = (item.get("media") or "").strip()
            # content 是单个网页的摘要证据，不是原文切片或跨结果总结。
            results.append(SearchResult(
                title=item.get("title"),
                url=item.get("link"),
                published_date=published_date,
                evidences=[content] if content else [],
                metadata={"media": media} if media else {},
            ))
        return SearchResponse(results=results)
