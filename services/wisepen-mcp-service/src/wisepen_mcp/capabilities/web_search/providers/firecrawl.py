from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from common.core.exceptions import ServiceException
from firecrawl import AsyncFirecrawl

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


class FirecrawlSearchTool(BaseSearchTool):
    tool_name = "firecrawl_search"
    tool_meta = ToolMeta(
        tool_name="firecrawl_search",
        policy=_WEB_SEARCH_POLICY_META,
        ui_spec=ToolUISpecMeta(display_name="Firecrawl 搜索"),
        config_spec=_WEB_SEARCH_REQUIRED_API_KEY_CONFIG,
        output_cache_spec=_WEB_SEARCH_OUTPUT_CACHE_SPEC,
        failure_reason="Firecrawl Search Failed",
    )
    provider_name = "firecrawl"
    # include_domains 与 exclude_domains 互斥
    description = BaseSearchTool.description + (
        "\nFor this tool, include_domains and exclude_domains are mutually exclusive.\n"
    )

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

        # include_domains 与 exclude_domains 互斥
        if include_domains and exclude_domains:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_INVALID,
                "Firecrawl include_domains and exclude_domains are mutually exclusive.",
            )
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, "Firecrawl API key is required.")

        search_options: dict[str, Any] = {}
        if include_domains:
            search_options["include_domains"] = include_domains
        if exclude_domains:
            search_options["exclude_domains"] = exclude_domains
        if recency is not None:
            search_options["tbs"] = {
                SearchRecency.DAY: "qdr:d",
                SearchRecency.WEEK: "qdr:w",
                SearchRecency.MONTH: "qdr:m",
                SearchRecency.YEAR: "qdr:y",
            }[recency]

        try:
            data = await AsyncFirecrawl(api_key=api_key).v2.search(
                query, limit=max_results, sources=["web"], **search_options
            )
        except Exception as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"firecrawl web request failed: {exc}") from exc

        return SearchResponse(
            results=[
                SearchResult(
                    title=item.title,
                    url=item.url,
                    evidences=[item.description] if item.description else [], # Firecrawl v2 默认把相关段落放入 description
                )
                for item in data.web or []
            ]
        )

    async def search_academic(
        self,
        *,
        query: str,
        max_results: int,
        api_key: str | None,
        recency: SearchRecency | None = None,
    ) -> SearchResponse:
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, "Firecrawl API key is required.")

        search_options: dict[str, Any] = {}
        if recency is not None:
            # 把四档 recency 统一映射成最近 N 天的滚动下限
            days = {
                SearchRecency.DAY: 1,
                SearchRecency.WEEK: 7,
                SearchRecency.MONTH: 30,
                SearchRecency.YEAR: 365,
            }[recency]
            today = datetime.now(timezone.utc).date()
            search_options = {
                "from_date": (today - timedelta(days=days - 1)).isoformat(),
                "to_date": today.isoformat(),
            }

        try:
            data = await AsyncFirecrawl(api_key=api_key).search_papers(
                query, k=max_results, **search_options
            )
        except Exception as exc:
            raise ServiceException(McpErrorCode.WEB_SEARCH_UNAVAILABLE, f"firecrawl paper request failed: {exc}") from exc

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
