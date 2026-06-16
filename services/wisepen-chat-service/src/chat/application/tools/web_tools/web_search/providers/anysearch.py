from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .helpers.coerce import as_dict_tuple, as_str, as_str_or_none, as_str_tuple
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
class AnySearchRequest(ProviderSearchRequest):
    """AnySearch search POST JSON 请求体。"""

    query: str  # 查询文本
    max_results: int = 10  # 最大结果数
    endpoint: SearchProviderEndpoint = SearchProviderEndpoint.WEB  # 内部路由标签

    @property
    def path(self) -> str:
        """返回 AnySearch endpoint path。"""
        return "/v1/search"

    def to_http_request(self) -> ProviderSearchHttpRequest:
        return ProviderSearchHttpRequest(
            method="POST",
            path=self.path,
            json={
                "query": self.query,
                "max_results": self.max_results,
                "content_types": ["webpage"],
            },
        )


def map_anysearch_response(
    data: dict[str, Any],
    *,
    query: str,
    endpoint: SearchProviderEndpoint,
    max_results: int,
) -> ProviderSearchResponse:
    """把 AnySearch 响应归一化为 provider 搜索响应。"""
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    answer = as_str_or_none(payload.get("answer"))
    items = [
        result
        for item in as_dict_tuple(payload.get("results"))
        if (result := _map_anysearch_item(item=item, answer=answer)) is not None
    ]
    return ProviderSearchResponse(
        query=query,
        provider=SearchProviderName.ANYSEARCH,
        endpoint=endpoint,
        results=dedupe_by_url(items, url_getter=lambda item: item.url, limit=max_results),
    )


def _map_anysearch_item(
    *,
    item: dict[str, Any],
    answer: str | None,
) -> ProviderSearchResult | None:
    """归一化 AnySearch 单条结果。"""
    title = as_str(item.get("title"))
    url = as_str(item.get("url"))
    if not is_valid_result(title=title, url=url):
        return None
    highlights = as_str_tuple(item.get("highlights"))
    return ProviderSearchResult(
        title=title,
        url=url,
        preview=SearchPreview(
            overview=as_str_or_none(item.get("snippet") or item.get("description") or item.get("summary")),
            highlights=highlights,
            answer=answer,
        ),
    )
