from __future__ import annotations

from dataclasses import dataclass
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
class SearchResult:
    """搜索源返回的一条标准化结果。"""

    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    highlights: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """搜索源返回的标准化响应。"""

    query: str
    provider: SearchProviderName | None
    results: tuple[SearchResult, ...] = ()
    answer: str | None = None


@dataclass(frozen=True, slots=True)
class WebSearchCandidate:
    candidate_id: str
    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    highlights: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class SearchPipelineResult:
    search_query: str
    response: SearchResponse | None
    candidates: tuple[WebSearchCandidate, ...]
