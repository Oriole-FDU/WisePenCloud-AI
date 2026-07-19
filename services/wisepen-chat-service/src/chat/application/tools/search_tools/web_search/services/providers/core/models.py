from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SearchProviderName(StrEnum):
    EXA = "exa"
    TAVILY = "tavily"
    ANYSEARCH = "anysearch"
    BAIDU_QIANFAN = "baidu_qianfan"


class SearchMode(StrEnum):
    WEB = "web"
    ACADEMIC = "academic"


@dataclass(frozen=True, slots=True)
class ProviderSearchHttpRequest:
    """Provider HTTP 请求描述。"""

    method: str
    path: str
    params: dict[str, object] | None = None
    json: dict[str, object] | None = None


class ProviderSearchRequest:
    """Provider 搜索请求抽象。"""

    def to_http_request(self) -> ProviderSearchHttpRequest:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class SearchPreview:
    """搜索结果预览信息。"""

    snippet: str | None = None
    highlights: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderSearchResult:
    """单条搜索结果。"""

    title: str
    url: str
    preview: SearchPreview = field(default_factory=SearchPreview)


@dataclass(frozen=True, slots=True)
class ProviderSearchResponse:
    """Provider 标准化搜索响应。"""

    query: str
    provider: SearchProviderName | None
    results: tuple[ProviderSearchResult, ...] = ()
    answer: str | None = None
