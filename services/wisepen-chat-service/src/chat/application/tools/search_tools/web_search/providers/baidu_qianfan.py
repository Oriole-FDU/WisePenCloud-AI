from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._utils.coerce import as_dict_tuple, as_str, as_str_or_none
from ._utils.search_result import dedupe_by_url, is_valid_result
from .models import (
    ProviderSearchHttpRequest,
    ProviderSearchRequest,
    ProviderSearchResponse,
    ProviderSearchResult,
    SearchPreview,
    SearchProviderName,
)


@dataclass(frozen=True, slots=True)
class BaiduQianfanSearchRequest(ProviderSearchRequest):
    """百度千帆 AI 搜索 `/v2/ai_search/web_search` POST JSON 请求体。"""

    query: str  # 查询文本
    max_results: int = 10  # web 类型搜索结果上限

    @property
    def path(self) -> str:
        return "/v2/ai_search/web_search"

    def to_http_request(self) -> ProviderSearchHttpRequest:
        payload: dict[str, object] = {
            "messages": [
                {
                    "role": "user",
                    "content": self.query,
                }
            ],
            "search_source": "baidu_search_v2",
            "resource_type_filter": [
                {
                    "type": "web",
                    "top_k": self.max_results,
                }
            ],
        }
        return ProviderSearchHttpRequest(
            method="POST",
            path=self.path,
            json=payload,
        )


def map_baidu_qianfan_response(
        data: dict[str, Any],
        *,
        query: str,
        max_results: int,
) -> ProviderSearchResponse:
    """把百度千帆 AI 搜索响应归一化为 provider 搜索响应。"""
    answer = as_str_or_none(data.get("answer")) or as_str_or_none(data.get("content"))
    items = [
        result
        for item in as_dict_tuple(data.get("references"))
        if (result := _map_reference(item=item)) is not None
    ]
    return ProviderSearchResponse(
        query=query,
        provider=SearchProviderName.BAIDU_QIANFAN,
        results=dedupe_by_url(items, url_getter=lambda item: item.url, limit=max_results),
        answer=answer,
    )


def _map_reference(
        *,
        item: dict[str, Any],
) -> ProviderSearchResult | None:
    reference_type = as_str(item.get("type") or item.get("resource_type")).lower()
    if reference_type and reference_type != "web":
        return None

    title = as_str(item.get("title"))
    url = as_str(item.get("url"))
    if not is_valid_result(title=title, url=url):
        return None
    return ProviderSearchResult(
        title=title,
        url=url,
        preview=SearchPreview(
            overview=as_str_or_none(item.get("content") or item.get("snippet")),
        ),
    )
