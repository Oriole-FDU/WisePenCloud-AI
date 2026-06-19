from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .utils.coerce import as_dict_tuple, as_str, as_str_or_none
from .utils.search_result import dedupe_by_url, is_valid_result
from .models import (
    ProviderSearchHttpRequest,
    ProviderSearchRequest,
    ProviderSearchResponse,
    ProviderSearchResult,
    SearchPreview,
    SearchProviderEndpoint,
    SearchProviderName,
)


# endpoint → Serper path 映射
_ENDPOINT_PATHS: dict[SearchProviderEndpoint, str] = {
    SearchProviderEndpoint.WEB: "/search",
    SearchProviderEndpoint.NEWS: "/news",
    SearchProviderEndpoint.SCHOLAR: "/scholar",
}

# endpoint → 响应中结果列表的字段名
_RESULT_KEYS: dict[SearchProviderEndpoint, str] = {
    SearchProviderEndpoint.WEB: "organic",
    SearchProviderEndpoint.NEWS: "news",
    SearchProviderEndpoint.SCHOLAR: "organic",
}


@dataclass(frozen=True, slots=True)
class SerperSearchRequest(ProviderSearchRequest):
    """Serper POST JSON 请求体。"""

    query: str
    max_results: int = 10
    endpoint: SearchProviderEndpoint = SearchProviderEndpoint.WEB

    def to_http_request(self) -> ProviderSearchHttpRequest:
        path = _ENDPOINT_PATHS.get(self.endpoint, "/search")
        return ProviderSearchHttpRequest(
            method="POST",
            path=path,
            json={
                "q": self.query,
                "num": self.max_results,
            },
        )


def map_serper_response(
    data: dict[str, Any],
    *,
    query: str,
    endpoint: SearchProviderEndpoint,
    max_results: int,
) -> ProviderSearchResponse:
    """把 Serper 响应归一化为 provider 搜索响应。"""
    result_key = _RESULT_KEYS.get(endpoint, "organic")
    items = [
        result
        for item in as_dict_tuple(data.get(result_key))
        if (result := _map_serper_item(item=item, endpoint=endpoint)) is not None
    ]
    return ProviderSearchResponse(
        query=query,
        provider=SearchProviderName.SERPER,
        endpoint=endpoint,
        results=dedupe_by_url(items, url_getter=lambda item: item.url, limit=max_results),
    )


def _map_serper_item(
    *,
    item: dict[str, Any],
    endpoint: SearchProviderEndpoint,
) -> ProviderSearchResult | None:
    """归一化 Serper 单条结果。"""
    title = as_str(item.get("title"))
    # scholar 模式下 URL 优先级: pdfUrl（PDF 直链）> htmlUrl > link（出版页）
    url = as_str(item.get("pdfUrl") or item.get("htmlUrl") or item.get("link"))
    if not is_valid_result(title=title, url=url):
        return None

    overview = as_str_or_none(item.get("snippet"))
    if endpoint == SearchProviderEndpoint.SCHOLAR:
        # scholar 模式附加学术元信息
        parts = [overview] if overview else []
        pub_info = as_str_or_none(item.get("publicationInfo"))
        if pub_info:
            parts.append(pub_info)
        cited_by = item.get("citedBy")
        if isinstance(cited_by, (int, float)):
            parts.append(f"Cited by {int(cited_by)}")
        overview = "\n".join(parts) if parts else None

    return ProviderSearchResult(
        title=title,
        url=url,
        preview=SearchPreview(overview=overview),
    )
