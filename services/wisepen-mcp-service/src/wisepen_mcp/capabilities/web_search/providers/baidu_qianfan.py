from __future__ import annotations

from typing import Any

import httpx
from common.core.exceptions import ServiceException

from wisepen_mcp.core.config.app_settings import settings
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


class BaiduQianfanSearchTool(BaseSearchTool):
    tool_name = "baidu_qianfan_search"
    tool_meta = ToolMeta(
        tool_name="baidu_qianfan_search",
        policy=_WEB_SEARCH_POLICY_META,
        ui_spec=ToolUISpecMeta(display_name="百度千帆搜索"),
        config_spec=_WEB_SEARCH_REQUIRED_API_KEY_CONFIG,
        output_cache_spec=_WEB_SEARCH_OUTPUT_CACHE_SPEC,
        failure_reason="Baidu Qianfan Search Failed",
    )
    provider_name = "baidu_qianfan"

    def __init__(self, *, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
        api_key: str | None,
        recency: SearchRecency | None = None,
        include_domains: list[str] | None = None,
    ) -> SearchResponse:
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, "Baidu Qianfan API key is required.")

        url = f"{settings.web_search.providers.baidu_qianfan.base_url.rstrip('/')}/v2/ai_search/web_search"
        payload = {
            "messages": [{"role": "user", "content": query}],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": max_results}],
        }
        if include_domains:
            payload["search_filter"] = {"match": {"site": include_domains}}

        if recency is not None:
            payload["search_recency_filter"] = recency.value

        try:
            response = await self._http_client.post(url, headers={"X-Appbuilder-Authorization": f"Bearer {api_key}"}, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code in {401, 403, 429}:
                raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, f"baidu_qianfan credential unavailable or quota exhausted: HTTP {status_code}") from exc
            if status_code >= 500:
                raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"baidu_qianfan remote service unavailable: HTTP {status_code}") from exc
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, f"baidu_qianfan request failed: HTTP {status_code}") from exc
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"baidu_qianfan network request failed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, f"baidu_qianfan request failed: {exc}") from exc

        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "baidu_qianfan response is not a JSON object.")
            return self.map_response(data)
        except ValueError as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "baidu_qianfan response is not valid JSON.") from exc
        except (KeyError, TypeError, AttributeError) as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "baidu_qianfan response JSON shape is invalid.") from exc

    @staticmethod
    def map_response(data: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            results=[
                SearchResult(
                    title=item.get("title"),
                    url=item.get("url"),
                    published_date=item.get("date"),
                    evidences=[item[key] for key in ("markdown_content", "content", "snippet") if item.get(key)][:1],
                    metadata={key: item[key] for key in ("authority_score", "rerank_score", "aladdin") if item.get(key) is not None},
                )
                for item in data["references"]
            ]
        )
