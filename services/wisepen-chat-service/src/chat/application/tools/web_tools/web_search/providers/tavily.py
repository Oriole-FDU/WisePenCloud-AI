from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .helpers.coerce import as_dict_tuple, as_str, as_str_or_none
from .helpers.search_result import dedupe_by_url, is_valid_result
from .models import (
    ProviderSearchHttpRequest,
    ProviderSearchRequest,
    ProviderSearchResponse,
    ProviderSearchResult,
    SearchPreview,
    SearchProviderEndpoint,
    SearchProviderName,
)


@dataclass(frozen=True, slots=True)
class TavilySearchRequest(ProviderSearchRequest):
    """Tavily Search API POST JSON 请求体。"""

    query: str  # 查询文本
    max_results: int = 10  # 最大结果数
    endpoint: SearchProviderEndpoint = SearchProviderEndpoint.WEB  # 内部路由标签

    @property
    def path(self) -> str:
        """返回 Tavily endpoint path。"""
        return "/search"

    def to_http_request(self) -> ProviderSearchHttpRequest:
        payload: dict[str, object] = {
            "query": self.query,
            "max_results": self.max_results,
            "include_answer": True,
            "include_raw_content": False,
            "include_images": False,
        }
        if self.endpoint == SearchProviderEndpoint.NEWS:
            payload["topic"] = "news"
        return ProviderSearchHttpRequest(
            method="POST",
            path=self.path,
            json=payload,
        )


def map_tavily_response(
    data: dict[str, Any],
    *,
    query: str,
    endpoint: SearchProviderEndpoint,
    max_results: int,
) -> ProviderSearchResponse:
    """把 Tavily search 响应归一化为 provider 搜索响应。"""
    answer = as_str_or_none(data.get("answer"))
    items = [
        result
        for item in as_dict_tuple(data.get("results"))
        if (result := _map_tavily_item(item=item, answer=answer)) is not None
    ]
    return ProviderSearchResponse(
        query=query,
        provider=SearchProviderName.TAVILY,
        endpoint=endpoint,
        results=dedupe_by_url(items, url_getter=lambda item: item.url, limit=max_results),
    )


def _map_tavily_item(
    *,
    item: dict[str, Any],
    answer: str | None,
) -> ProviderSearchResult | None:
    """归一化 Tavily 单条结果。"""
    title = as_str(item.get("title"))
    url = as_str(item.get("url"))
    if not is_valid_result(title=title, url=url):
        return None
    return ProviderSearchResult(
        title=title,
        url=url,
        preview=SearchPreview(
            overview=as_str_or_none(item.get("content")),
            answer=answer,
        ),
    )
