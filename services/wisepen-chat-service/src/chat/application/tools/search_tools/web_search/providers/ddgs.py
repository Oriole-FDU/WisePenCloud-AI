from __future__ import annotations

from typing import Any

from ._utils.coerce import as_str, as_str_or_none
from ._utils.search_result import dedupe_by_url, is_valid_result
from .models import (
    ProviderSearchResponse,
    ProviderSearchResult,
    SearchPreview,
)


def map_ddgs_response(
        items: list[dict[str, Any]],
        *,
        query: str,
        max_results: int,
) -> ProviderSearchResponse:
    """把 ddgs 原始结果列表归一化为 provider 搜索响应。

    ddgs text 返回字段: title / href / body
    """
    results = [
        result
        for item in items
        if (result := _map_ddgs_item(item=item)) is not None
    ]
    results = dedupe_by_url(results, url_getter=lambda r: r.url, limit=max_results)
    return ProviderSearchResponse(
        query=query,
        provider=None,
        results=results,
    )


def _map_ddgs_item(
        *,
        item: dict[str, Any],
) -> ProviderSearchResult | None:
    """归一化 ddgs 单条结果。"""
    title = as_str(item.get("title"))
    url = as_str(item.get("href"))
    if not is_valid_result(title=title, url=url):
        return None
    return ProviderSearchResult(
        title=title,
        url=url,
        preview=SearchPreview(
            overview=as_str_or_none(item.get("body")),
        ),
    )
