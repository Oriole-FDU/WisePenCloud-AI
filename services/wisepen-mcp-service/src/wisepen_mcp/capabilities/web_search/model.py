from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

DEFAULT_SEARCH_RESULTS = 10
MAX_SEARCH_RESULTS = 20

SearchDomain = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        pattern=(
            r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
            r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+"
        ),
    ),
]


class SearchMode(StrEnum):
    WEB = "web"  # 普通网页
    ACADEMIC = "academic"  # 学术内容


class SearchRecency(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class WebSearchRequest(BaseModel):
    """工具对外暴露的输入契约"""
    model_config = ConfigDict(frozen=True, extra="forbid")
    query: str = Field(
        min_length=1,
        description="Concise, specific search terms targeting the needed information.",
    )
    mode: SearchMode = Field(
        default=SearchMode.WEB,
        description=(
            "Search scope: use academic for scholarly publications "
            "or web for general external resources."
        ),
    )
    focus: str | None = Field(
        default=None,
        description=(
            "Specific fact, metric, comparison, or passage to prioritize "
            "within matched sources."
        ),
    )
    recency: SearchRecency | None = Field(
        default=None,
        description="Soft freshness preference: day, week, month, or year. Omit for no time restriction.",
    )
    include_domains: list[SearchDomain] | None = Field(
        default=None,
        description=(
            "Restrict sources to these hostnames and their subdomains, "
            "e.g. ['python.org', 'github.com']. No URLs, paths, ports, or wildcards. "
            "Omit or use [] for no restriction."
        ),
    )
    exclude_domains: list[SearchDomain] | None = Field(
        default=None,
        description=(
            "Exclude these hostnames and their subdomains. "
            "No URLs, paths, ports, or wildcards. Omit or use [] for no exclusions."
        ),
    )
    max_results: int = Field(
        default=DEFAULT_SEARCH_RESULTS,
        ge=1,
        le=MAX_SEARCH_RESULTS,
        description="Maximum number of source results to return.",
    )


class SearchResult(BaseModel):
    """单条搜索结果"""
    model_config = ConfigDict(frozen=True, extra="forbid")
    title: str | None = Field(
        default=None,
        description="Page or document title reported by the search provider.",
    )
    url: str | None = Field(
        default=None,
        description="Source URL for opening, verification, or citation.",
    )
    published_date: str | None = Field(
        default=None,
        description="Publication date when available.",
    )
    evidences: list[str] = Field(
        default_factory=list,
        description=(
            "Relevant excerpts, passages, highlights, or provider-prepared content "
            "that may directly support factual reasoning."
        ),
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-specific source metadata and relevance signals.",
    )


class SearchResponse(BaseModel):
    """provider 返回包"""
    model_config = ConfigDict(frozen=True, extra="forbid")
    results: list[SearchResult] = Field(
        description=(
            "Source results in the order returned by the search provider; "
            "deduplicated by URL and truncated to the requested count."
        ),
    )
    summary: str | None = Field(
        default=None,
        description=(
            "Optional provider-generated overview. Treat it only as a lead; "
            "factual claims should be supported by source results."
        ),
    )


class WebSearchToolResult(SearchResponse):
    """MCP 输出"""
    query: str = Field(
        description="Normalized query sent to the search provider.",
    )
    mode: SearchMode = Field(
        description="Search scope actually used for this request.",
    )
