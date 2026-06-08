from dataclasses import replace
from typing import List

from chat.application.algorithms.ranking.mmr import MmrCandidate, select_by_mmr
from chat.application.rag.runtime.retrieval.models import RagEvidence


class RagEvidenceSelector:
    """Select final diverse evidence with MMR."""

    def select(
        self,
        *,
        evidences: List[RagEvidence],
        top_k: int,
        lambda_param: float,
    ) -> List[RagEvidence]:
        if not evidences:
            return []

        candidates = [
            MmrCandidate(
                id=evidence.evidence_id,
                relevance_score=evidence.rerank_score,
                similarity_text=evidence.text,
                group_key=(
                    f"{evidence.resource_kind.value}:"
                    f"{evidence.resource_id}:"
                    f"{evidence.parent_chunk_id}"
                ),
            )
            for evidence in evidences
        ]

        selected = select_by_mmr(
            candidates,
            top_k=top_k,
            lambda_mult=lambda_param,
        )

        evidence_map = {evidence.evidence_id: evidence for evidence in evidences}
        return [
            replace(
                evidence_map[item.id],
                rank=item.rank,
                rerank_score=item.relevance_score,
                mmr_score=item.mmr_score,
                diversity_penalty=item.diversity_penalty,
            )
            for item in selected
        ]
