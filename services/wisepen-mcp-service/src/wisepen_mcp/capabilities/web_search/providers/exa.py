from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from common.core.exceptions import ServiceException
from exa_py import AsyncExa

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


class ExaSearchTool(BaseSearchTool):
    tool_name = "exa_search"
    tool_meta = ToolMeta(
        tool_name="exa_search",
        policy=_WEB_SEARCH_POLICY_META,
        ui_spec=ToolUISpecMeta(display_name="Exa 搜索"),
        config_spec=_WEB_SEARCH_REQUIRED_API_KEY_CONFIG,
        output_cache_spec=_WEB_SEARCH_OUTPUT_CACHE_SPEC,
        failure_reason="Exa Search Failed",
    )
    provider_name = "exa"

    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
        api_key: str | None,
        focus: str | None = None,
        recency: SearchRecency | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> SearchResponse:
        return await self._search(
            query=query,
            focus=focus,
            api_key=api_key,
            max_results=max_results,
            academic=False,
            recency=recency,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
        )

    async def search_academic(
        self,
        *,
        query: str,
        max_results: int,
        api_key: str | None,
        focus: str | None = None,
        recency: SearchRecency | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> SearchResponse:
        return await self._search(
            query=query,
            focus=focus,
            api_key=api_key,
            max_results=max_results,
            academic=True,
            recency=recency,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
        )

    async def _search(
        self,
        *,
        query: str,
        focus: str | None,
        max_results: int,
        api_key: str | None,
        academic: bool,
        recency: SearchRecency | None,
        include_domains: list[str] | None,
        exclude_domains: list[str] | None,
    ) -> SearchResponse:
        if not api_key:
            raise ServiceException(McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID, "Exa API key is required.")

        search_options = {}
        if include_domains:
            search_options["include_domains"] = include_domains
        if exclude_domains:
            search_options["exclude_domains"] = exclude_domains
        if recency is not None:
            # 把四档 recency 统一映射成最近 N 天的滚动下限
            days = {
                SearchRecency.DAY: 1,
                SearchRecency.WEEK: 7,
                SearchRecency.MONTH: 30,
                SearchRecency.YEAR: 365,
            }[recency]
            start = datetime.now(timezone.utc) - timedelta(days=days)
            search_options["start_published_date"] = start.isoformat()

        try:
            response = await AsyncExa(api_key=api_key).search(
                query,
                type="auto", # 让 Exa 自己在 neural/keyword 之间选
                num_results=max_results,
                category="research paper" if academic else None, # 学术模式限定论文，网页模式传 None 不限制
                # 不取全文/摘要，只拿高亮
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
