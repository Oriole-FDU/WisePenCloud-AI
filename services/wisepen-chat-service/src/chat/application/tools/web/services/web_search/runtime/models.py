from __future__ import annotations

from dataclasses import dataclass
from typing import List

from chat.application.tools.web.services.web_search.enums import QueryRole
from chat.application.tools.web.services.web_search.models import SearchResponse


@dataclass(frozen=True, slots=True)
class QueryVariant:
    """分发给底层检索服务的独立查询变体实体。

    每个变体包含查询文本、角色（PRIMARY/SECONDARY 等）、
    最大结果数和权重，供下游并发调度和精排使用。
    """

    id: str
    text: str
    role: QueryRole
    max_results: int
    weight: float


@dataclass(frozen=True, slots=True)
class VariantSearchResponse:
    """搜索变体结果返回包。"""

    variant: QueryVariant
    response: SearchResponse
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class SearchResultCandidate:
    """搜索变体召回的原始候选实体。"""

    id: str
    url: str
    canonical_url: str
    title: str
    snippet: str
    provider: str
    source_query: str
    query_role: QueryRole
    original_rank: int


@dataclass(frozen=True, slots=True)
class RankedSearchResultCandidate:
    """经过精排流水线计算后的最终候选。"""

    candidate: SearchResultCandidate
    rrf_score: float
    rank: int
    rrf_sources: List[str]


