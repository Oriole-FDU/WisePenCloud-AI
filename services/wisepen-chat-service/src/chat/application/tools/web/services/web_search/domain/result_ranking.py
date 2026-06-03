from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Set
from urllib.parse import urlparse

from chat.application.algorithms.hash import stable_hash
from chat.application.algorithms.ranking.fielded_bm25 import FieldedItem, rank_fielded_bm25
from chat.application.algorithms.ranking.rrf import RRF_K, RankedList, weighted_rrf
from chat.application.tools.web.services.web_search.domain.query_planning import (
    QUERY_VARIANT_WEIGHTS,
)
from chat.application.tools.web.services.web_search.domain.variant_execution import (
    VariantSearchResponse,
)
from chat.application.tools.web.services.web_search.enums import QueryRole, SearchMode
from chat.application.tools.web.utils.urls import canonicalize_url

URL_RRF_WEIGHTS = {
    "source_original": 1.0,
    "metadata_bm25": 1.0,
}

METADATA_FIELD_WEIGHTS = {
    "title": 2.0,
    "snippet": 1.0,
    "url_path": 0.3,
}


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


class SearchResultRankingPipeline:
    """搜索结果的精排流水线。

    将多个变体的召回结果合并、去重、按 BM25 + RRF 排序，
    最终按域名限频输出前 N 条。
    """
    def __init__(
        self,
        *,
        mode: str,
        merged_limit: int,
        max_urls_per_domain: int = 2,
    ) -> None:
        """初始化 SearchResultRankingPipeline。

        Args:
            mode: 搜索模式，用于确定变体权重。
            merged_limit: 最终输出的最大结果数。
            max_urls_per_domain: 每域名最多保留的结果数。
        """
        self._mode = SearchMode(mode)
        self._merged_limit = merged_limit
        self._max_urls_per_domain = max_urls_per_domain

    def run(
        self,
        variant_responses: List[VariantSearchResponse],
    ) -> List[RankedSearchResultCandidate]:
        """执行精排流水线：合并候选 → 按查询变体分组 → BM25+RRF 排序 → 域名去重 → 截断。

        Args:
            variant_responses: 各变体的搜索结果列表。

        Returns:
            精排后的最终候选列表。
        """
        all_candidates: List[SearchResultCandidate] = []
        for variant_response in variant_responses:
            provider = variant_response.response.source
            for rank, result in enumerate(variant_response.response.results):
                canonical_url = canonicalize_url(result.url)
                all_candidates.append(
                    SearchResultCandidate(
                        id=stable_hash(canonical_url),
                        url=result.url,
                        canonical_url=canonical_url,
                        title=result.title,
                        snippet=result.snippet,
                        provider=provider,
                        source_query=variant_response.variant.text,
                        query_role=variant_response.variant.role,
                        original_rank=rank,
                    )
                )

        if not all_candidates:
            return []

        query_groups = defaultdict(list)
        for candidate in all_candidates:
            key = (candidate.source_query, candidate.query_role)
            query_groups[key].append(candidate)

        variant_rankings = [
            (
                role,
                self._rerank_urls_for_single_query(
                    query=query_text,
                    candidates=group,
                ),
            )
            for (query_text, role), group in query_groups.items()
        ]

        ranked_urls = (
            variant_rankings[0][1]
            if len(variant_rankings) == 1
            else self._fuse_query_variant_rankings(variant_rankings)
        )

        seen_urls: Set[str] = set()
        deduped_urls: List[RankedSearchResultCandidate] = []
        for item in ranked_urls:
            if item.candidate.canonical_url not in seen_urls:
                seen_urls.add(item.candidate.canonical_url)
                deduped_urls.append(item)

        counts: Dict[str, int] = defaultdict(int)
        selected: List[RankedSearchResultCandidate] = []

        for item in deduped_urls:
            url_str = item.candidate.canonical_url
            parsed = urlparse(url_str)
            domain = parsed.netloc if parsed.netloc else (url_str.split("/", 1)[0])

            if counts[domain] >= self._max_urls_per_domain:
                continue

            counts[domain] += 1
            selected.append(item)

            if len(selected) >= self._merged_limit:
                break

        return selected

    def _rerank_urls_for_single_query(
        self,
        *,
        query: str,
        candidates: List[SearchResultCandidate],
    ) -> List[RankedSearchResultCandidate]:
        """对单个查询变体的候选结果执行 BM25 语义排序 + RRF 融合。

        先按规范 URL 去重，再对元数据（标题/摘要/URL）做 BM25 排序，
        最后与原始排序通过加权 RRF 融合。

        Args:
            query: 查询文本。
            candidates: 该查询变体的候选结果列表。

        Returns:
            排序后的候选结果列表。
        """
        by_url: Dict[str, SearchResultCandidate] = {}
        for candidate in candidates:
            existing = by_url.get(candidate.canonical_url)
            if existing is None or candidate.original_rank < existing.original_rank:
                by_url[candidate.canonical_url] = candidate

        metadata_items: List[FieldedItem] = []
        for candidate in by_url.values():
            parsed_path = urlparse(candidate.url).path
            cleaned_path = (
                parsed_path.replace("/", " ")
                .replace("-", " ")
                .replace("_", " ")
                .replace(".", " ")
            )
            metadata_items.append(
                FieldedItem(
                    id=candidate.id,
                    fields={
                        "title": candidate.title,
                        "snippet": candidate.snippet,
                        "url_path": cleaned_path,
                    },
                )
            )

        bm25_ranked_ids = rank_fielded_bm25(
            query,
            metadata_items,
            METADATA_FIELD_WEIGHTS,
        )
        source_ranked_ids = [
            candidate.id
            for candidate in sorted(by_url.values(), key=lambda item: item.original_rank)
        ]

        rrf_items = weighted_rrf(
            [
                RankedList(
                    name="source_original",
                    ids=source_ranked_ids,
                    weight=URL_RRF_WEIGHTS["source_original"],
                ),
                RankedList(
                    name="metadata_bm25",
                    ids=bm25_ranked_ids,
                    weight=URL_RRF_WEIGHTS["metadata_bm25"],
                ),
            ],
            k=RRF_K,
        )

        by_id = {candidate.id: candidate for candidate in by_url.values()}
        return [
            RankedSearchResultCandidate(
                candidate=by_id[item.id],
                rrf_score=item.score,
                rank=item.rank,
                rrf_sources=item.sources,
            )
            for item in rrf_items
            if item.id in by_id
        ]

    def _fuse_query_variant_rankings(
        self,
        variant_rankings,
    ) -> List[RankedSearchResultCandidate]:
        """将多个查询变体的排序结果通过加权 RRF 融合为单一排序列表。

        Args:
            variant_rankings: 各变体的 (role, ranked_list) 元组列表。

        Returns:
            融合后的候选结果列表。
        """
        weights = QUERY_VARIANT_WEIGHTS[self._mode]

        ranked_lists = [
            RankedList(
                name=f"variant:{role}",
                ids=[item.candidate.id for item in ranked_urls],
                weight=weights[role],
            )
            for role, ranked_urls in variant_rankings
        ]

        rrf_items = weighted_rrf(ranked_lists, k=RRF_K)

        by_id: Dict[str, SearchResultCandidate] = {}
        for _, ranked_urls in variant_rankings:
            for item in ranked_urls:
                by_id.setdefault(item.candidate.id, item.candidate)

        return [
            RankedSearchResultCandidate(
                candidate=by_id[item.id],
                rrf_score=item.score,
                rank=item.rank,
                rrf_sources=item.sources,
            )
            for item in rrf_items
            if item.id in by_id
        ]
