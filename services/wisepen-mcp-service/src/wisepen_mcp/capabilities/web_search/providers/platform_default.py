from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from common.core.exceptions import ServiceException

from wisepen_mcp.core.config.app_settings import settings
from wisepen_mcp.domain.error_codes import McpErrorCode

from ..search_tools import BaseSearchTool, SearchRecency, SearchResponse, SearchResult


class PlatformSearchTool(BaseSearchTool):
    """平台托管的默认搜索，通过 DeepSeek Search 返回摘要证据与来源评分。"""

    tool_name = "default_web_search"
    provider_name = None
    requires_api_key = False  # 不要求调用方提供 BYOK；托管密钥在 Provider 请求前检查。

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
        # 默认工具只使用平台托管凭证，请求侧 api_key 不得覆盖平台配置。
        api_key = settings.WEB_SEARCH_API_KEY.strip()
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CONFIG_MISSING, "Platform Web Search API key is not configured.")

        # 使用官方平铺语法组合域名，不人为添加可能改变搜索行为的括号。
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
            # DeepSeek 通过查询语法按日期过滤；保留已有语法，由供应商解释组合条件。
            after = (datetime.now(timezone.utc) - timedelta(days=days)).date()
            query = f"{query} after:{after.isoformat()}"

        # 限制包含域名与日期语法；拒绝超长查询，避免截断关键词或搜索操作符。
        if len(query) > 128:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_INVALID,
                "DeepSeek query must be at most 128 characters, including domain and "
                "recency filters. Shorten the query or reduce the domains and try again.",
            )

        url = f"{settings.WEB_SEARCH_DEEPSEEK_BASE_URL.rstrip('/')}/v1/tools/search"
        payload = {
            "query": query,
            "count": max_results,
            # Search 只返回摘要证据；全文读取由平台 Web Fetch 能力负责。
            "need_content": False,
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
            # 只有合法的 results 空数组代表无结果，错误结构不能被当成空搜索。
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
            # 单条长摘要优先，短摘要仅补缺；不读取供应商可能返回的全文 content。
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
