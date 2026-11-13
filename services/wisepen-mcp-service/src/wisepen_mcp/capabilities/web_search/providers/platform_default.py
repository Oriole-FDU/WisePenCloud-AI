from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from common.core.exceptions import ServiceException

from wisepen_mcp.core.config.app_settings import settings
from wisepen_mcp.domain.error_codes import McpErrorCode

from ..model import SearchRecency, SearchResponse, SearchResult
from ..search_tools import (
    BaseSearchTool,
    ToolMeta,
    ToolUISpecMeta,
    _WEB_SEARCH_OPTIONAL_API_KEY_CONFIG,
    _WEB_SEARCH_OUTPUT_CACHE_SPEC,
    _WEB_SEARCH_POLICY_META,
)


class PlatformSearchTool(BaseSearchTool):
    """平台托管的默认搜索，通过 DeepSeek Search 返回摘要证据与来源评分"""

    tool_name = "default_web_search"
    tool_meta = ToolMeta(
        tool_name="default_web_search",
        policy=_WEB_SEARCH_POLICY_META,
        ui_spec=ToolUISpecMeta(display_name="默认 Web 搜索「由 Deepseek 提供」"),
        config_spec=_WEB_SEARCH_OPTIONAL_API_KEY_CONFIG,
        output_cache_spec=_WEB_SEARCH_OUTPUT_CACHE_SPEC,
        failure_reason="Default Web Search Failed",
    )
    provider_name = None
    requires_api_key = False  # 调用方 key 可选，未配置时由平台密钥兜底

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
        exclude_domains: list[str] | None = None,
    ) -> SearchResponse:
        # 优先使用调用方配置的 DeepSeek key；未配置时回退到平台托管密钥
        api_key = (api_key or "").strip() or settings.web_search.platform_default.api_key.strip()
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CONFIG_MISSING, "Platform Web Search API key is not configured.")

        # 使用官方平铺语法组合域名
        if include_domains:
            query += " " + " OR ".join(f"site:{domain}" for domain in include_domains)
        if exclude_domains:
            query += " " + " ".join(f"-site:{domain}" for domain in exclude_domains)

        if recency is not None:
            days = {
                SearchRecency.DAY: 1,
                SearchRecency.WEEK: 7,
                SearchRecency.MONTH: 30,
                SearchRecency.YEAR: 365,
            }[recency]
            after = (datetime.now(timezone.utc) - timedelta(days=days)).date()
            query = f"{query} after:{after.isoformat()}"

        # 拒绝超长查询，避免截断关键词或搜索操作符
        if len(query) > 128:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_INVALID,
                "DeepSeek query must be at most 128 characters, including domain and "
                "recency filters. Shorten the query or reduce the domains and try again.",
            )

        url = f"{settings.web_search.platform_default.base_url.rstrip('/')}/v1/tools/search"
        payload = {
            "query": query,
            "count": max_results,
            "need_content": False, # Search 只返回摘要证据
        }

        try:
            response = await self._http_client.post(url, headers={"Authorization": f"Bearer {api_key}"}, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 400:
                raise ServiceException(McpErrorCode.WEB_SEARCH_INVALID, "DeepSeek search parameters are invalid; check the query and try again.") from exc
            if status_code in {401, 402, 403}:
                raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, f"DeepSeek credential unavailable or account balance insufficient: HTTP {status_code}") from exc
            if status_code in {408, 429} or status_code >= 500:
                raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"DeepSeek remote service unavailable: HTTP {status_code}") from exc
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, f"DeepSeek request failed: HTTP {status_code}") from exc
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, "DeepSeek network request failed.") from exc
        except httpx.HTTPError as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "DeepSeek request failed.") from exc

        try:
            data = response.json()
            # 只有合法的 results 空数组代表无结果
            if not isinstance(data, dict) or not isinstance(data["results"], list):
                raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "DeepSeek response must contain a results array.")
            return self.map_response(data)
        except ValueError as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "DeepSeek response is not valid JSON.") from exc
        except (KeyError, TypeError, AttributeError) as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_FAILED, "DeepSeek response JSON shape is invalid.") from exc

    @staticmethod
    def map_response(data: dict[str, Any]) -> SearchResponse:
        results: list[SearchResult] = []
        for item in data["results"]:
            # 单条长摘要优先，短摘要仅补缺
            evidence = (item.get("summary") or "").strip() or (item.get("snippet") or "").strip()
            metadata = {
                key: item[key]
                for key in ["rerank_score", "authority"]
                if item.get(key) is not None
            }
            site_name = (item.get("site_name") or "").strip()
            if site_name:
                metadata["site_name"] = site_name
            results.append(SearchResult(
                title=item["title"],
                url=item["url"],
                published_date=item.get("date"),
                evidences=[evidence] if evidence else [],
                metadata=metadata,
            ))
        return SearchResponse(results=results)
