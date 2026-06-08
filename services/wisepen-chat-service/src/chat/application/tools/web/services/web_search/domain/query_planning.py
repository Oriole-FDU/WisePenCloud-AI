from dataclasses import dataclass
from typing import Dict, List, Optional

from chat.application.tools.web.services.web_search.enums import QueryRole, SearchMode

QUERY_VARIANT_MAX_RESULTS: Dict[SearchMode, Dict[QueryRole, int]] = {
    SearchMode.FAST: {
        QueryRole.PRIMARY: 10,
    },
    SearchMode.NORMAL: {
        QueryRole.PRIMARY: 12,
        QueryRole.SECONDARY: 6,
    },
    SearchMode.DEEP: {
        QueryRole.PRIMARY: 30,
        QueryRole.SECONDARY: 15,
        QueryRole.EXTRA_1: 10,
        QueryRole.EXTRA_2: 10,
    },
}
QUERY_VARIANT_WEIGHTS: Dict[SearchMode, Dict[QueryRole, float]] = {
    SearchMode.FAST: {
        QueryRole.PRIMARY: 1.0,
    },
    SearchMode.NORMAL: {
        QueryRole.PRIMARY: 1.0,
        QueryRole.SECONDARY: 1.0,
    },
    SearchMode.DEEP: {
        QueryRole.PRIMARY: 1.0,
        QueryRole.SECONDARY: 1.0,
        QueryRole.EXTRA_1: 1.0,
        QueryRole.EXTRA_2: 1.0,
    },
}
GROUNDING_BUDGET: Dict[SearchMode, Dict[str, int]] = {
    SearchMode.FAST: {"max_keywords": 0, "max_extract_chars_per_keyword": 0},
    SearchMode.NORMAL: {"max_keywords": 1, "max_extract_chars_per_keyword": 600},
    SearchMode.DEEP: {"max_keywords": 3, "max_extract_chars_per_keyword": 800},
}
MERGED_CANDIDATE_LIMIT: Dict[SearchMode, int] = {
    SearchMode.FAST: 10,
    SearchMode.NORMAL: 20,
    SearchMode.DEEP: 40,
}


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
class SearchPlan:
    """描述一次搜索调用的查询变体集合和维基百科背景关键词对齐计划。"""
    
    mode: str
    query_variants: List[QueryVariant]
    wikipedia_keywords: List[str]


def build_search_plan(
    *,
    mode: str,
    queries: List[str],
    wikipedia_keywords: Optional[List[str]] = None,
) -> SearchPlan:
    """按搜索模式构建查询变体列表和维基百科关键词预算。

    Args:
        mode: 搜索模式（FAST / NORMAL / DEEP）。
        queries: 搜索查询字符串列表，按角色优先级排序。
        wikipedia_keywords: 可选的维基百科背景关键词列表。

    Returns:
        包含查询变体和关键词的 SearchPlan。
    """
    variant_budget = QUERY_VARIANT_MAX_RESULTS[SearchMode(mode)]
    variant_weights = QUERY_VARIANT_WEIGHTS[SearchMode(mode)]

    variants: List[QueryVariant] = [
        QueryVariant(
            id=f"v{i}",
            text=query,
            role=role,
            max_results=variant_budget[role],
            weight=variant_weights[role],
        )
        for i, (role, query) in enumerate(zip(variant_budget.keys(), queries))
    ]

    max_keywords = GROUNDING_BUDGET[SearchMode(mode)]["max_keywords"]
    selected_keywords = (wikipedia_keywords or [])[:max_keywords]

    return SearchPlan(
        mode=mode,
        query_variants=variants,
        wikipedia_keywords=selected_keywords,
    )
