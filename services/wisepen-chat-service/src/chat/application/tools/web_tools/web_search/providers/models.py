from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SearchProviderName(StrEnum):
    """Web search provider 标识。"""

    FOURGET = "4get"
    EXA = "exa"
    TAVILY = "tavily"
    ANYSEARCH = "anysearch"


class SearchProviderEndpoint(StrEnum):
    """Provider 内部垂类端点。"""

    WEB = "web"
    NEWS = "news"
    SCHOLAR = "scholar"


@dataclass(frozen=True, slots=True)
class ProviderSearchHttpRequest:
    """provider search request 的 HTTP 表达。"""

    method: str  # HTTP method
    path: str  # provider endpoint path
    params: dict[str, object] | None = None  # GET query 参数
    json: dict[str, object] | None = None  # POST JSON body


class ProviderSearchRequest:
    """provider 请求对象接口。"""

    def to_http_request(self) -> ProviderSearchHttpRequest:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class SearchPreview:
    """搜索结果的模型可见预览。"""

    overview: str | None = None  # provider 真实返回的摘要/简介/snippet
    highlights: tuple[str, ...] = ()  # provider 返回的高亮片段
    answer: str | None = None  # provider 真实返回的直接答案


@dataclass(frozen=True, slots=True)
class ProviderSearchResult:
    """模型最终消费的搜索结果。"""

    title: str  # 结果标题
    url: str  # 结果 URL，后续 web fetch 依赖该字段
    preview: SearchPreview = field(default_factory=SearchPreview)  # 模型可消费预览


@dataclass(frozen=True, slots=True)
class ProviderSearchResponse:
    """供应商搜索响应的归一化包装。"""

    query: str  # 本次查询文本
    provider: SearchProviderName  # 响应来源 provider
    endpoint: SearchProviderEndpoint  # 响应垂类端点
    results: tuple[ProviderSearchResult, ...] = ()  # 模型最终消费的结果列表
    source_id: str | None = None  # 来源标识，区分平台内置源和用户自定义源
