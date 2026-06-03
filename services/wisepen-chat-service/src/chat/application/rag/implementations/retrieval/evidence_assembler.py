import asyncio
from typing import Dict, List

from chat.application.algorithms.ranking.mmr import MmrSelectedItem
from chat.application.rag.domain.evidence_hydration import (
    RagHydratedCandidate,
    SearchAndParentLookups,
)
from chat.application.rag.domain.evidence_output import RagEvidence
from chat.application.rag.domain.index_chunks import RetrieveChunk
from chat.application.rag.domain.parent_aggregation import ParentCandidate
from chat.application.rag.domain.ports import (
    RagChunkRepository,
    RagNeighborChunkLookup,
    RagRetrieveChunkLookup,
    RagSearchChunkLookup,
)
from chat.application.rag.domain.reranking import RerankableDocument


class EvidenceAssemblyError(RuntimeError):
    """RAG Evidence 组装失败。"""


class RagEvidenceAssembler:
    """RAG Evidence 组装器。

    - 输入 ParentCandidate。
    - 第一阶段批量读取 best child search record 和 parent chunk。
    - 第二阶段使用 parent_chunk.chunk_index 批量读取 neighbor chunks。
    - 不依赖 ParentCandidate / SearchChunkHit 持有 chunk_index。
    """

    def __init__(self, chunk_repository: RagChunkRepository) -> None:
        """初始化对象依赖。"""
        self._chunk_repository = chunk_repository

    async def hydrate_candidates(
        self,
        *,
        parent_candidates: List[ParentCandidate],
        neighbor_before: int,
        neighbor_after: int,
    ) -> List[RagHydratedCandidate]:
        """处理当前流程。"""
        if not parent_candidates:
            return []

        lookups = _build_search_and_parent_lookups(
            parent_candidates=parent_candidates,
        )

        search_records, parent_chunks = await asyncio.gather(
            self._chunk_repository.get_search_chunk_records(lookups.search_lookups),
            self._chunk_repository.get_retrieve_chunks(lookups.parent_lookups),
        )

        neighbor_lookups = _build_neighbor_lookups(
            parent_candidates=parent_candidates,
            parent_chunks=parent_chunks,
            neighbor_before=neighbor_before,
            neighbor_after=neighbor_after,
        )

        neighbor_chunks = await self._chunk_repository.get_neighbor_retrieve_chunks_batch(
            neighbor_lookups
        )

        hydrated_candidates: List[RagHydratedCandidate] = []

        for parent_candidate in parent_candidates:
            candidate_id = _build_parent_candidate_id(parent_candidate)
            best_child_hit = parent_candidate.best_child_hit

            search_record = search_records.get(candidate_id)
            parent_chunk = parent_chunks.get(candidate_id)
            candidate_neighbor_chunks = neighbor_chunks.get(candidate_id, [])

            if search_record is None:
                raise EvidenceAssemblyError(
                    f"Search chunk record not found: {best_child_hit.chunk_id}"
                )

            if parent_chunk is None:
                raise EvidenceAssemblyError(
                    f"Parent retrieve chunk not found: {parent_candidate.chunk_id}"
                )

            neighbor_text = "\n\n".join(
                chunk.text
                for chunk in candidate_neighbor_chunks
            )

            rerank_text = (
                f"Retrieval context:\n{search_record.retrieval_context}\n\n"
                f"Matched search chunk:\n{search_record.chunk.text}\n\n"
                f"Parent retrieve chunk:\n{parent_chunk.text}\n\n"
                f"Neighbor chunks:\n{neighbor_text}"
            )

            mmr_text = (
                f"{search_record.retrieval_context}\n\n"
                f"{search_record.chunk.text}\n\n"
                f"{parent_chunk.text}"
            )

            hydrated_candidates.append(
                RagHydratedCandidate(
                    candidate_id=candidate_id,
                    parent_candidate=parent_candidate,
                    search_chunk_record=search_record,
                    parent_chunk=parent_chunk,
                    neighbor_chunks=candidate_neighbor_chunks,
                    rerank_text=rerank_text,
                    mmr_text=mmr_text,
                    group_key=(
                        f"{parent_candidate.user_id}:"
                        f"{parent_candidate.resource_id}:"
                        f"{parent_candidate.index_version}:"
                        f"{parent_candidate.chunk_id}"
                    ),
                )
            )

        return hydrated_candidates

    def build_rerankable_documents(
        self,
        hydrated_candidates: List[RagHydratedCandidate],
    ) -> List[RerankableDocument]:
        """构建当前流程。"""
        documents: List[RerankableDocument] = []
        seen_ids = set()

        for hydrated_candidate in hydrated_candidates:
            if hydrated_candidate.candidate_id in seen_ids:
                raise EvidenceAssemblyError(
                    f"Duplicate hydrated parent candidate id: {hydrated_candidate.candidate_id}"
                )

            seen_ids.add(hydrated_candidate.candidate_id)

            documents.append(
                RerankableDocument(
                    id=hydrated_candidate.candidate_id,
                    text=hydrated_candidate.rerank_text,
                )
            )

        return documents

    def build_evidences(
        self,
        *,
        hydrated_candidates: List[RagHydratedCandidate],
        selected_items: List[MmrSelectedItem],
    ) -> List[RagEvidence]:
        """构建当前流程。"""
        hydrated_map = {
            candidate.candidate_id: candidate
            for candidate in hydrated_candidates
        }

        evidences: List[RagEvidence] = []

        for item in selected_items:
            hydrated_candidate = hydrated_map[item.id]
            parent_candidate = hydrated_candidate.parent_candidate
            best_child_hit = parent_candidate.best_child_hit
            search_record = hydrated_candidate.search_chunk_record
            parent_chunk = hydrated_candidate.parent_chunk

            evidences.append(
                RagEvidence(
                    evidence_id=item.id,
                    rank=item.rank,
                    user_id=parent_candidate.user_id,
                    resource_kind=parent_candidate.resource_kind,
                    resource_id=parent_candidate.resource_id,
                    index_version=parent_candidate.index_version,
                    chunk_id=best_child_hit.chunk_id,
                    parent_chunk_id=parent_candidate.chunk_id,
                    parent_chunk_index=parent_chunk.chunk_index,
                    chunk_index=search_record.chunk.chunk_index,
                    text=parent_chunk.text,
                    search_text=search_record.chunk.text,
                    retrieval_context=search_record.retrieval_context,
                    neighbor_texts=[
                        chunk.text
                        for chunk in hydrated_candidate.neighbor_chunks
                    ],
                    rerank_score=item.relevance_score,
                    mmr_score=item.mmr_score,
                    diversity_penalty=item.diversity_penalty,
                    rrf_score=parent_candidate.rrf_score,
                    matched_channels=parent_candidate.matched_channels,
                    matched_queries=parent_candidate.matched_queries,
                )
            )

        return evidences


def _build_search_and_parent_lookups(
    *,
    parent_candidates: List[ParentCandidate],
) -> SearchAndParentLookups:
    """批量生成 search record 和 parent chunk lookup。"""

    search_lookups: List[RagSearchChunkLookup] = []
    parent_lookups: List[RagRetrieveChunkLookup] = []

    for parent_candidate in parent_candidates:
        candidate_id = _build_parent_candidate_id(parent_candidate)
        best_child_hit = parent_candidate.best_child_hit

        search_lookups.append(
            RagSearchChunkLookup(
                lookup_id=candidate_id,
                user_id=parent_candidate.user_id,
                resource_kind=parent_candidate.resource_kind,
                resource_id=parent_candidate.resource_id,
                index_version=parent_candidate.index_version,
                chunk_id=best_child_hit.chunk_id,
            )
        )

        parent_lookups.append(
            RagRetrieveChunkLookup(
                lookup_id=candidate_id,
                user_id=parent_candidate.user_id,
                resource_kind=parent_candidate.resource_kind,
                resource_id=parent_candidate.resource_id,
                index_version=parent_candidate.index_version,
                chunk_id=parent_candidate.chunk_id,
            )
        )

    return SearchAndParentLookups(
        search_lookups=search_lookups,
        parent_lookups=parent_lookups,
    )


def _build_neighbor_lookups(
    *,
    parent_candidates: List[ParentCandidate],
    parent_chunks: Dict[str, RetrieveChunk],
    neighbor_before: int,
    neighbor_after: int,
) -> List[RagNeighborChunkLookup]:
    """基于已水合 parent_chunk.chunk_index 构造 neighbor lookup。"""

    neighbor_lookups: List[RagNeighborChunkLookup] = []

    for parent_candidate in parent_candidates:
        candidate_id = _build_parent_candidate_id(parent_candidate)
        parent_chunk = parent_chunks.get(candidate_id)

        if parent_chunk is None:
            raise EvidenceAssemblyError(
                f"Parent retrieve chunk not found: {parent_candidate.chunk_id}"
            )

        neighbor_lookups.append(
            RagNeighborChunkLookup(
                lookup_id=candidate_id,
                user_id=parent_candidate.user_id,
                resource_kind=parent_candidate.resource_kind,
                resource_id=parent_candidate.resource_id,
                index_version=parent_candidate.index_version,
                center_chunk_index=parent_chunk.chunk_index,
                before=neighbor_before,
                after=neighbor_after,
            )
        )

    return neighbor_lookups


def _build_parent_candidate_id(parent_candidate: ParentCandidate) -> str:
    """构建当前流程。"""
    return (
        f"{parent_candidate.user_id}:"
        f"{parent_candidate.resource_kind.value}:"
        f"{parent_candidate.resource_id}:"
        f"{parent_candidate.index_version}:"
        f"{parent_candidate.chunk_id}"
    )
