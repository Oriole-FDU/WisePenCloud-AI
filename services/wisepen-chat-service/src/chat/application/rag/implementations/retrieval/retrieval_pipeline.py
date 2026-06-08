from typing import List

from chat.application.algorithms.ranking.mmr import (
    MmrCandidate,
    select_by_mmr,
)
from chat.application.rag.domain.answerability import (
    EvidenceSufficiencyEvaluator,
)
from chat.application.rag.domain.candidate_fusion import RagCandidateFusion
from chat.application.rag.domain.parent_aggregation import RagParentAggregator
from chat.application.rag.domain.retrieval_execution import RagRetrievalPipelineResult
from chat.application.rag.domain.retrieval_hits import ChannelRetrievalResult
from chat.application.rag.domain.retrieval_planning import RagRetrievalQuery
from .evidence_assembler import RagEvidenceAssembler
from .reranker import ZeroEntropyReranker
from .retrieval_orchetrator import RagRetrievalOrchestrator


class RagRetrievalPipeline:
    """RAG 检索流水线。

    - 任何中间环节失败都视为失败，返回空结果
    """

    def __init__(
            self,
            retrieval_orchestrator: RagRetrievalOrchestrator,
            candidate_fusion: RagCandidateFusion,
            evidence_assembler: RagEvidenceAssembler,
            reranker: ZeroEntropyReranker,
            sufficiency_evaluator: EvidenceSufficiencyEvaluator,
            parent_aggregator: RagParentAggregator,
    ) -> None:
        """初始化对象依赖。"""
        self._retrieval_orchestrator = retrieval_orchestrator
        self._candidate_fusion = candidate_fusion
        self._evidence_assembler = evidence_assembler
        self._reranker = reranker
        self._sufficiency_evaluator = sufficiency_evaluator
        self._parent_aggregator = parent_aggregator

    async def retrieve(self, query: RagRetrievalQuery) -> RagRetrievalPipelineResult:
        """RAG 混合检索、重排与多样性网格组装流。"""

        # 异步多通道召回
        channel_results = await self._retrieval_orchestrator.retrieve_channels(query)

        # RRF 算法候选融合
        fused_candidates = self._candidate_fusion.fuse(
            mode=query.mode,
            channel_results=channel_results,
            top_k=query.fusion_top_k,
        )

        if not fused_candidates:
            return self._build_empty_result(query, channel_results)

        # 异步补全上下文水合
        parent_candidates = self._parent_aggregator.aggregate(
            fused_candidates=fused_candidates,
            top_k=query.fusion_top_k,
        )

        if not parent_candidates:
            return self._build_empty_result(query, channel_results)

        hydrated_candidates = await self._evidence_assembler.hydrate_candidates(
            parent_candidates=parent_candidates,
            neighbor_before=query.neighbor_before,
            neighbor_after=query.neighbor_after,
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

        if not reranked_documents:
            return self._build_empty_result(query, channel_results)

        hydrated_map = {c.candidate_id: c for c in hydrated_candidates}

        # 组装 MMR 多样性算子输入
        mmr_candidates = [
            MmrCandidate(
                id=doc.id,
                relevance_score=doc.score,
                similarity_text=hydrated_map[doc.id].mmr_text,
                group_key=hydrated_map[doc.id].group_key,
            )
            for doc in reranked_documents
        ]

        # MMR 语义去重
        selected_items = select_by_mmr(
            mmr_candidates,
            top_k=query.final_top_k,
            lambda_mult=query.mmr_lambda,
        )

        if not selected_items:
            return self._build_empty_result(query, channel_results)

        # 回连水合实体，包装输出最终 Evidence 证据链
        evidences = self._evidence_assembler.build_evidences(
            hydrated_candidates=hydrated_candidates,
            selected_items=selected_items,
        )

        sufficiency = self._sufficiency_evaluator.evaluate(
            query=query,
            evidences=evidences,
        )

        return RagRetrievalPipelineResult(
            query=query,
            channel_results=channel_results,
            evidences=evidences,
            sufficiency=sufficiency,
        )

    def _build_empty_result(
            self,
            query: RagRetrievalQuery,
            channel_results: List[ChannelRetrievalResult],
    ) -> RagRetrievalPipelineResult:
        """空结果构造"""
        return RagRetrievalPipelineResult(
            query=query,
            channel_results=channel_results,
            evidences=[],
            sufficiency=self._sufficiency_evaluator.evaluate(
                query=query,
                evidences=[],
            ),
        )
