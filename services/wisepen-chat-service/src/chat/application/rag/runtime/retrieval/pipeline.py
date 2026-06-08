from __future__ import annotations

from dataclasses import dataclass
from typing import List

from chat.application.rag.permissions import can_view
from chat.application.rag.runtime.retrieval.channels.models import (
    ChannelRetrievalDiagnostic,
    ChannelRetrievalResult,
)
from chat.application.rag.runtime.retrieval.channels.orchestrator import RagRetrievalOrchestrator
from chat.application.rag.runtime.retrieval.enums import RetrievalChannel
from chat.application.rag.runtime.retrieval.models import RagEvidence, RagRetrievalQuery
from chat.application.rag.runtime.retrieval.stages.answerability import (
    EvidenceSufficiencyEvaluator, SufficiencyResult,
)
from chat.application.rag.runtime.retrieval.stages.candidate_fusion import RagCandidateFusion
from chat.application.rag.runtime.retrieval.stages.evidence_assembler import RagEvidenceAssembler
from chat.application.rag.runtime.retrieval.stages.evidence_selector import RagEvidenceSelector
from chat.application.rag.runtime.retrieval.stages.parent_aggregation import RagParentAggregator
from chat.application.rag.runtime.retrieval.stages.reranker import ZeroEntropyReranker
from common.logger import log_event


class RagRetrievalPipeline:
    """RAG 检索流水线。

    - 任何中间环节失败都视为失败，返回空结果
    """

    def __init__(
            self,
            retrieval_orchestrator: RagRetrievalOrchestrator,
            candidate_fusion: RagCandidateFusion,
            evidence_assembler: RagEvidenceAssembler,
            evidence_selector: RagEvidenceSelector,
            reranker: ZeroEntropyReranker,
            sufficiency_evaluator: EvidenceSufficiencyEvaluator,
            parent_aggregator: RagParentAggregator,
    ) -> None:
        """初始化对象依赖。"""
        self._retrieval_orchestrator = retrieval_orchestrator
        self._candidate_fusion = candidate_fusion
        self._evidence_assembler = evidence_assembler
        self._evidence_selector = evidence_selector
        self._reranker = reranker
        self._sufficiency_evaluator = sufficiency_evaluator
        self._parent_aggregator = parent_aggregator

    async def retrieve(self, query: RagRetrievalQuery) -> RagRetrievalPipelineResult:
        """RAG 混合检索、重排与多样性网格组装流。"""

        # 异步多通道召回
        channel_execution = await self._retrieval_orchestrator.retrieve_channels(query)
        channel_results = channel_execution.channel_results
        channel_diagnostics = channel_execution.diagnostics
        scope_by_key = {
            _scope_key(scope.user_id, scope.resource_kind, scope.resource_id, scope.index_version): scope
            for scope in channel_execution.scopes
        }
        diagnostics: List[RagRetrievalDiagnosticItem] = []
        log_event(
            "rag retrieval pipeline stage",
            user_id=query.user_id,
            stage="channel_retrieval",
            channel_count=len(channel_results),
            candidate_count=sum(len(result.candidates) for result in channel_results),
        )

        # RRF 算法候选融合
        channel_weights = query.channel_weights or {
            result.channel: 1.0 for result in channel_results
        }
        fused_candidates = self._candidate_fusion.fuse(
            channel_results=channel_results,
            channel_weights=channel_weights,
            top_k=query.fusion_top_k,
        )
        log_event(
            "rag retrieval pipeline stage",
            user_id=query.user_id,
            stage="candidate_fusion",
            candidate_count=len(fused_candidates),
        )

        if not fused_candidates:
            return self._build_empty_result(
                query,
                channel_results,
                channel_diagnostics,
                diagnostics,
            )

        diagnostics.extend(_build_fusion_diagnostics(fused_candidates))

        # 异步补全上下文水合
        parent_candidates = self._parent_aggregator.aggregate(
            fused_candidates=fused_candidates,
            top_k=query.fusion_top_k,
        )
        log_event(
            "rag retrieval pipeline stage",
            user_id=query.user_id,
            stage="parent_aggregation",
            candidate_count=len(parent_candidates),
        )

        if not parent_candidates:
            return self._build_empty_result(
                query,
                channel_results,
                channel_diagnostics,
                diagnostics,
            )

        hydrated_candidates = await self._evidence_assembler.hydrate_candidates(
            parent_candidates=parent_candidates,
            neighbor_before=query.neighbor_before,
            neighbor_after=query.neighbor_after,
        )
        log_event(
            "rag retrieval pipeline stage",
            user_id=query.user_id,
            stage="evidence_hydration",
            candidate_count=len(hydrated_candidates),
        )

        # 提取重排输入并执行精排
        rerankable_documents = self._evidence_assembler.build_rerankable_documents(
            hydrated_candidates
        )
        reranked_documents = await self._reranker.rerank(
            query=query.query,
            documents=rerankable_documents,
            top_n=query.rerank_top_n,
        )
        log_event(
            "rag retrieval pipeline stage",
            user_id=query.user_id,
            stage="rerank",
            input_count=len(rerankable_documents),
            candidate_count=len(reranked_documents),
        )

        if not reranked_documents:
            return self._build_empty_result(
                query,
                channel_results,
                channel_diagnostics,
                diagnostics,
            )

        hydrated_map = {c.candidate_id: c for c in hydrated_candidates}
        diagnostics.extend(_build_rerank_diagnostics(reranked_documents, hydrated_map))

        candidate_evidences = self._evidence_assembler.build_evidences(
            hydrated_candidates=hydrated_candidates,
            reranked_documents=reranked_documents,
        )

        evidences = self._evidence_selector.select(
            evidences=candidate_evidences,
            top_k=query.final_top_k,
            lambda_param=query.mmr_lambda,
        )
        log_event(
            "rag retrieval pipeline stage",
            user_id=query.user_id,
            stage="mmr_selection",
            input_count=len(candidate_evidences),
            candidate_count=len(evidences),
        )

        if not evidences:
            return self._build_empty_result(
                query,
                channel_results,
                channel_diagnostics,
                diagnostics,
            )

        diagnostics.extend(_build_mmr_diagnostics(evidences))

        evidences = [
            evidence
            for evidence in evidences
            if _can_read_evidence(
                evidence=evidence,
                query=query,
                scope_by_key=scope_by_key,
            )
        ]
        log_event(
            "rag retrieval pipeline stage",
            user_id=query.user_id,
            stage="permission_filter",
            candidate_count=len(evidences),
        )

        if not evidences:
            return self._build_empty_result(
                query,
                channel_results,
                channel_diagnostics,
                diagnostics,
            )

        sufficiency = self._sufficiency_evaluator.evaluate(
            query=query,
            evidences=evidences,
        )
        log_event(
            "rag retrieval pipeline completed",
            user_id=query.user_id,
            evidence_count=len(evidences),
            sufficient=sufficiency.sufficient,
            insufficient_reason=(
                sufficiency.reason.value if sufficiency.reason is not None else None
            ),
        )

        return RagRetrievalPipelineResult(
            query=query,
            channel_results=channel_results,
            channel_diagnostics=channel_diagnostics,
            evidences=evidences,
            sufficiency=sufficiency,
            diagnostics=diagnostics,
        )

    def _build_empty_result(
            self,
            query: RagRetrievalQuery,
            channel_results: List[ChannelRetrievalResult],
            channel_diagnostics,
            diagnostics: List[RagRetrievalDiagnosticItem],
    ) -> RagRetrievalPipelineResult:
        """空结果构造"""
        return RagRetrievalPipelineResult(
            query=query,
            channel_results=channel_results,
            channel_diagnostics=channel_diagnostics,
            evidences=[],
            sufficiency=self._sufficiency_evaluator.evaluate(
                query=query,
                evidences=[],
            ),
            diagnostics=diagnostics,
        )


@dataclass(frozen=True, slots=True)
class RagRetrievalDiagnosticItem:
    """Ranking diagnostic item."""

    stage: str
    rank: int
    candidate_id: str
    resource_id: str
    chunk_id: str
    parent_chunk_id: str
    score: float
    sources: List[RetrievalChannel]


def _build_fusion_diagnostics(fused_candidates) -> List[RagRetrievalDiagnosticItem]:
    items: List[RagRetrievalDiagnosticItem] = []
    for fused_candidate in fused_candidates[:10]:
        candidate = fused_candidate.candidate
        items.append(
            RagRetrievalDiagnosticItem(
                stage="fusion",
                rank=fused_candidate.rank,
                candidate_id=candidate.chunk_id,
                resource_id=candidate.resource_id,
                chunk_id=candidate.chunk_id,
                parent_chunk_id=candidate.parent_chunk_id,
                score=round(fused_candidate.rrf_score, 6),
                sources=fused_candidate.sources,
            )
        )
    return items


def _scope_key(user_id, resource_kind, resource_id: str, index_version: str) -> str:
    return f"{user_id}:{resource_kind.value}:{resource_id}:{index_version}"


def _can_read_evidence(*, evidence, query: RagRetrievalQuery, scope_by_key) -> bool:
    scope = scope_by_key.get(
        _scope_key(
            evidence.user_id,
            evidence.resource_kind,
            evidence.resource_id,
            evidence.index_version,
        )
    )
    if scope is None:
        return False
    return can_view(
        user_id=query.user_id,
        group_role_map=query.group_role_map,
        projection=scope.acl_projection,
    )


def _build_rerank_diagnostics(
    reranked_documents,
    hydrated_map,
) -> List[RagRetrievalDiagnosticItem]:
    items: List[RagRetrievalDiagnosticItem] = []
    for doc in reranked_documents[:10]:
        hydrated = hydrated_map[doc.id]
        parent_candidate = hydrated.parent_candidate
        best_child_hit = parent_candidate.best_child_hit
        items.append(
            RagRetrievalDiagnosticItem(
                stage="rerank",
                rank=doc.rank,
                candidate_id=doc.id,
                resource_id=parent_candidate.resource_id,
                chunk_id=best_child_hit.chunk_id,
                parent_chunk_id=parent_candidate.chunk_id,
                score=round(doc.score, 6),
                sources=parent_candidate.matched_channels,
            )
        )
    return items


def _build_mmr_diagnostics(evidences) -> List[RagRetrievalDiagnosticItem]:
    items: List[RagRetrievalDiagnosticItem] = []
    for evidence in evidences[:10]:
        items.append(
            RagRetrievalDiagnosticItem(
                stage="mmr",
                rank=evidence.rank,
                candidate_id=evidence.evidence_id,
                resource_id=evidence.resource_id,
                chunk_id=evidence.chunk_id,
                parent_chunk_id=evidence.parent_chunk_id,
                score=round(evidence.mmr_score, 6),
                sources=evidence.matched_channels,
            )
        )
    return items


@dataclass(frozen=True, slots=True)
class RagRetrievalPipelineResult:
    """Full retrieval pipeline result."""

    query: RagRetrievalQuery
    channel_results: List[ChannelRetrievalResult]
    channel_diagnostics: List[ChannelRetrievalDiagnostic]
    evidences: List[RagEvidence]
    sufficiency: SufficiencyResult
    diagnostics: List[RagRetrievalDiagnosticItem]
