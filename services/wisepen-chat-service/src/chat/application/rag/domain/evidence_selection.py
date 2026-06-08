from typing import List

from chat.application.algorithms.ranking.mmr import MmrCandidate, select_by_mmr
from chat.application.rag.domain.evidence_output import RagEvidence


class RagEvidenceSelector:
    """RAG 证据多样性选择器。

    基于 Maximal Marginal Relevance (MMR) 算法，
    在相关性和多样性之间取得平衡，选择最终展示给用户的证据列表。
    """
    def select(
        self,
        *,
        evidences: List[RagEvidence],
        top_k: int,
        lambda_param: float,
    ) -> List[RagEvidence]:
        """使用 MMR 算法选择多样性证据。

        算法流程：
        1. 将 RagEvidence 转换为 MmrCandidate（提取重排得分作为相关性分数，文本用于相似度计算）
        2. 按 (resource_kind, resource_id, parent_chunk_id) 构建分组键以控制组内多样性
        3. 调用 select_by_mmr 执行 MMR 贪心选择
        4. 将选中结果还原为 RagEvidence（更新 rank、mmr_score、diversity_penalty）

        Args:
            evidences: 候选证据列表（已按重排得分排序）。
            top_k: 最终选择的证据数量上限。
            lambda_param: MMR lambda 参数，0=最大多样性，1=最大相关性。

        Returns:
            按 rank 排序的最终证据列表。
        """
        candidates = [
            MmrCandidate(
                id=ev.evidence_id,
                relevance_score=ev.rerank_score,
                similarity_text=ev.text,
                group_key=f"{ev.resource_kind.value}:{ev.resource_id}:{ev.parent_chunk_id}",
            )
            for ev in evidences
        ]

        selected = select_by_mmr(
            candidates=candidates,
            top_k=min(top_k, len(candidates)),
            lambda_mult=lambda_param,
        )

        ev_map = {ev.evidence_id: ev for ev in evidences}
        result = []
        for rank, item in enumerate(selected, 1):
            ev = ev_map[item.id]
            result.append(RagEvidence(
                evidence_id=ev.evidence_id,
                rank=rank,
                user_id=ev.user_id,
                resource_kind=ev.resource_kind,
                resource_id=ev.resource_id,
                index_version=ev.index_version,
                chunk_id=ev.chunk_id,
                parent_chunk_id=ev.parent_chunk_id,
                parent_chunk_index=ev.parent_chunk_index,
                chunk_index=ev.chunk_index,
                text=ev.text,
                search_text=ev.search_text,
                retrieval_context=ev.retrieval_context,
                neighbor_texts=ev.neighbor_texts,
                rerank_score=item.relevance_score,
                mmr_score=item.mmr_score,
                diversity_penalty=item.diversity_penalty,
                rrf_score=ev.rrf_score,
                matched_channels=ev.matched_channels,
                matched_queries=ev.matched_queries,
            ))
        return result
