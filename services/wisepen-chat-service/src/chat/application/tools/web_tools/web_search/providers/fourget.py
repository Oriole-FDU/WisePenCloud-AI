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
class FourGetSearchRequest(ProviderSearchRequest):
    """4get `/api/v1/web` 与 `/api/v1/news` GET 参数。

    4get 官方 API 文档说明：所有 API endpoint 使用 GET，web UI 的 `/web` 请求可替换为
    `/api/v1/web` 得到 JSON；新闻端点为 `/api/v1/news`。
    """

    query: str  # 4get 搜索关键字，映射为 s 参数
    endpoint: SearchProviderEndpoint = SearchProviderEndpoint.WEB  # web 或 news
    max_results: int = 10  # 统一请求接口字段，4get 请求体不消费
    scraper: str = "ddg"  # 默认使用 DDG 后端，提高本地 4get 成功率

    @property
    def path(self) -> str:
        """返回 4get API 路径。"""
        return f"/api/v1/{self.endpoint.value}"

    def to_http_request(self) -> ProviderSearchHttpRequest:
        return ProviderSearchHttpRequest(
            method="GET",
            path=self.path,
            params={
                "s": self.query,
                "scraper": self.scraper,
            },
        )


def map_fourget_response(
    data: dict[str, Any],
    *,
    query: str,
    endpoint: SearchProviderEndpoint,
    max_results: int,
) -> ProviderSearchResponse:
    """把 4get JSON 响应归一化为 provider 搜索响应。"""
    raw_items = data.get("web" if endpoint == SearchProviderEndpoint.WEB else "news")
    answer = _map_fourget_answers(data.get("answer"))
    items = [
        result
        for item in as_dict_tuple(raw_items)
        if (result := _map_fourget_item(item=item, answer=answer)) is not None
    ]
    results = dedupe_by_url(items, url_getter=lambda item: item.url, limit=max_results)
    return ProviderSearchResponse(
        query=query,
        provider=SearchProviderName.FOURGET,
        endpoint=endpoint,
        results=results,
    )


def _map_fourget_item(
    *,
    item: dict[str, Any],
    answer: str | None,
) -> ProviderSearchResult | None:
    """归一化 4get web/news 单条结果。"""
    title = as_str(item.get("title"))
    url = as_str(item.get("url"))
    if not is_valid_result(title=title, url=url):
        return None
    return ProviderSearchResult(
        title=title,
        url=url,
        preview=SearchPreview(
            overview=as_str_or_none(item.get("description")),
            answer=answer,
        ),
    )


def _map_fourget_answers(value: object) -> str | None:
    """把 4get answer 节点压成短参考答案。"""
    parts: list[str] = []
    for answer in as_dict_tuple(value):
        title = as_str(answer.get("title"))
        if title:
            parts.append(title)
        for node in as_dict_tuple(answer.get("description")):
            value_text = as_str(node.get("value"))
            if value_text:
                parts.append(value_text)
    return "\n".join(parts).strip() or None
