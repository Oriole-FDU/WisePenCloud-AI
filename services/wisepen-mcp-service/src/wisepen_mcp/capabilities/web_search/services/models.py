from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field


class SearchProviderName(StrEnum):
    EXA = "exa"
    TAVILY = "tavily"
    ANYSEARCH = "anysearch"
    BAIDU_QIANFAN = "baidu_qianfan"
    TINYFISH = "tinyfish"
    FIRECRAWL = "firecrawl"


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
    response: SearchResponse
    candidates: tuple[WebSearchCandidate, ...]


class WebSearchCandidateResult(BaseModel):
    candidate_id: str = Field(
        description="Result label to use when referring to this candidate."
    )
    title: str | None = Field(
        default=None,
        description="Page or document title reported by the search provider.",
    )
    url: str | None = Field(
        default=None,
        description="Source URL for opening or citing the result.",
    )
    snippet: str | None = Field(
        default=None,
        description="Provider excerpt for judging whether the source is relevant.",
    )
    highlights: tuple[str, ...] | None = Field(
        default=None,
        description="Additional excerpts that directly matched the search.",
    )


class WebSearchToolResult(BaseModel):
    query: str = Field(
        description="Normalized query that was sent to the search provider."
    )
    mode: SearchMode = Field(description="Search scope used for this request.")
    candidates: tuple[WebSearchCandidateResult, ...] = Field(
        description=(
            "Search evidence ordered by relevance to ranking_query; inspect each "
            "candidate's URL and excerpts before relying on it."
        )
    )
    supplier_answer: str | None = Field(
        default=None,
        description=(
            "Optional provider-generated summary. Treat it as a lead and verify it "
            "against the returned candidates."
        ),
    )
