from __future__ import annotations

from typing import Any

from .utils.coerce import as_str, as_str_or_none
from .utils.search_result import dedupe_by_url, is_valid_result
from .models import (
    ProviderSearchResponse,
    ProviderSearchResult,
    SearchPreview,
    SearchProviderEndpoint,
    SearchProviderName,
)


def map_ddgs_response(
    items: list[dict[str, Any]],
    *,
    query: str,
    endpoint: SearchProviderEndpoint,
    max_results: int,
) -> ProviderSearchResponse:
    """把 ddgs 原始结果列表归一化为 provider 搜索响应。

    ddgs text 返回字段: title / href / body
    ddgs news 返回字段: date / title / body / url / image / source
    """
    url_key = "url" if endpoint == SearchProviderEndpoint.NEWS else "href"
    results = [
        result
        for item in items
        if (result := _map_ddgs_item(item=item, url_key=url_key)) is not None
    ]
    results = dedupe_by_url(results, url_getter=lambda r: r.url, limit=max_results)
    return ProviderSearchResponse(
        query=query,
        provider=SearchProviderName.FOUGET_DDG,
        endpoint=endpoint,
        results=results,
    )


def _map_ddgs_item(
    *,
    item: dict[str, Any],
    url_key: str,
) -> ProviderSearchResult | None:
    """归一化 ddgs 单条结果。"""
    title = as_str(item.get("title"))
    url = as_str(item.get(url_key))
    if not is_valid_result(title=title, url=url):
        return None
    return ProviderSearchResult(
        title=title,
        url=url,
        preview=SearchPreview(
            overview=as_str_or_none(item.get("body")),
        ),
    )
