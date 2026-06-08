from collections import defaultdict
from dataclasses import dataclass
from typing import List

from chat.application.rag.domain.candidate_fusion import RagFusedCandidate
from chat.application.rag.domain.enums import RetrievalChannel
from chat.application.rag.domain.retrieval_hits import SearchChunkHit


@dataclass(frozen=True, slots=True)
class ParentCandidate:
    """父块候选。

    将多条子块（SearchChunk）按父块（RetrieveChunk）聚合后的结果，
    保留命中信息最优的一条子块记录作为代表。
    """

    user_id: str
    resource_kind: str
    index_version: str
    chunk_id: str
    resource_id: str
    best_child_hit: SearchChunkHit
    matched_channels: List[RetrievalChannel]
    matched_queries: List[str]
    rrf_score: float


class RagParentAggregator:
    """父块聚合器。

    将多路召回融合后的候选按父块 ID 分组聚合，
    一组子块对应一个父块候选，以减少冗余并保留完整的命中通道和查询信息。
    """
    def aggregate(
        self,
        *,
        fused_candidates: List[RagFusedCandidate],
        top_k: int,
    ) -> List[ParentCandidate]:
        """将融合候选按父块分组聚合。

        算法流程：
        1. 按 (user_id, resource_kind, resource_id, index_version, parent_chunk_id) 分组
        2. 对每组取最佳子块构建 ParentCandidate（以 RRF 分数和 rank 为指标）
        3. 按 RRF 分数降序、rank 升序排序后取 top_k

        Args:
            fused_candidates: RRF 融合后的候选列表。
            top_k: 聚合后保留的父块候选数量。

        Returns:
            按 RRF 分数降序排列的父块候选列表。
        """
        grouped_candidates = defaultdict(list)
        for fused_candidate in fused_candidates:
            candidate = fused_candidate.candidate
            parent_id = (
                f"{candidate.user_id}:"
                f"{candidate.resource_kind.value}:"
                f"{candidate.resource_id}:"
                f"{candidate.index_version}:"
                f"{candidate.parent_chunk_id}"
            )
            grouped_candidates[parent_id].append(fused_candidate)

        parent_candidates = [
            self._build_parent_candidate(child_candidates)
            for child_candidates in grouped_candidates.values()
        ]

        return sorted(
            parent_candidates,
            key=lambda c: (
                -c.rrf_score,
                c.best_child_hit.rank,
                c.chunk_id,
            ),
        )[:top_k]

    def _build_parent_candidate(
        self,
        child_candidates: List[RagFusedCandidate],
    ) -> ParentCandidate:
        """从一组同父块的子候选构建父块候选。

        选取 rrf_score 最高且 rank 最小的子候选作为 best_child_hit，
        合并所有子候选的 matched_channels 和 matched_queries（去重保留顺序）。

        Args:
            child_candidates: 属于同一父块的一组融合候选。

        Returns:
            聚合后的父块候选。
        """
        best_candidate = max(child_candidates, key=lambda c: (c.rrf_score, -c.rank))
        best_cand_item = best_candidate.candidate

        best_child_hit = SearchChunkHit(
            user_id=best_cand_item.user_id,
            resource_kind=best_cand_item.resource_kind,
            index_version=best_cand_item.index_version,
            chunk_id=best_cand_item.chunk_id,
            parent_chunk_id=best_cand_item.parent_chunk_id,
            resource_id=best_cand_item.resource_id,
            channel=best_cand_item.channel,
            rank=best_candidate.rank,
            score=best_cand_item.score,
            matched_query=best_cand_item.matched_query,
        )

        matched_channels = list(dict.fromkeys(c.candidate.channel for c in child_candidates))
        matched_queries = list(dict.fromkeys(c.candidate.matched_query for c in child_candidates))

        return ParentCandidate(
            user_id=best_cand_item.user_id,
            resource_kind=best_cand_item.resource_kind,
            index_version=best_cand_item.index_version,
            chunk_id=best_cand_item.parent_chunk_id,
            resource_id=best_cand_item.resource_id,
            best_child_hit=best_child_hit,
            matched_channels=matched_channels,
            matched_queries=matched_queries,
            rrf_score=best_candidate.rrf_score,
        )
