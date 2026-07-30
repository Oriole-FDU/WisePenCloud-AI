from __future__ import annotations

from typing import TypedDict, cast

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models
from qdrant_client.conversions import common_types as qdrant_types

from chat.application.rag.retrieval import (
    RagCandidateRequest,
    RagPermissionFilterBuilder,
    RagRetrievalCandidate,
)
from chat.application.utils.ranking import ScoreSignal, ScoreSignalKind

_PAYLOAD_FIELDS = (
    "content_revision",
    "resource_id",
    "chunk_id",
    "reading_block_id",
    "raw_text",
    "section_id",
    "section_path",
    "anchor_labels",
    "source_ref_id",
)


class _CandidatePayload(TypedDict):
    content_revision: str
    resource_id: str
    chunk_id: str
    reading_block_id: str
    raw_text: str
    section_id: str
    section_path: list[str]
    anchor_labels: list[str]
    source_ref_id: str


class QdrantRagCandidateRepository:
    __slots__ = (
        "_bm25_options",
        "_client",
        "_collection_name",
        "_dense_vector_name",
        "_permission_filter_builder",
        "_sparse_vector_name",
    )

    def __init__(
        self,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
        permission_filter_builder: RagPermissionFilterBuilder,
        bm25_config: qdrant_models.Bm25Config,
        dense_vector_name: str = "dense",
        sparse_vector_name: str = "sparse",
    ) -> None:
        if client.cloud_inference is not True:
            raise ValueError("Qdrant server-side BM25 inference must be enabled")
        self._client = client
        self._collection_name = collection_name
        self._permission_filter_builder = permission_filter_builder
        self._bm25_options = bm25_config.model_dump(
            mode="json",
            exclude_none=True,
        )
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name

    async def retrieve_candidates(
        self,
        request: RagCandidateRequest,
    ) -> tuple[RagRetrievalCandidate, ...]:
        if request.limit <= 0:
            return ()
        if not await self._client.collection_exists(self._collection_name):
            return ()

        query_filter = self._build_filter(request)
        response = await self._client.query_points(
            collection_name=self._collection_name,
            prefetch=[
                qdrant_models.Prefetch(
                    query=list(request.query_vector),
                    using=self._dense_vector_name,
                    filter=query_filter,
                    limit=request.limit,
                ),
                qdrant_models.Prefetch(
                    query=qdrant_models.Document(
                        text=request.query_text,
                        model="qdrant/bm25",
                        options=self._bm25_options,
                    ),
                    using=self._sparse_vector_name,
                    filter=query_filter,
                    limit=request.limit,
                ),
            ],
            query=qdrant_models.FusionQuery(fusion=qdrant_models.Fusion.RRF),
            limit=request.limit,
            with_payload=list(_PAYLOAD_FIELDS),
        )
        return _to_candidates(tuple(response.points))

    def _build_filter(self, request: RagCandidateRequest) -> qdrant_models.Filter:
        must: list[qdrant_models.Condition] = [
            self._permission_filter_builder.build_qdrant_filter(
                request.permission_scope
            )
        ]
        resource_ids = tuple(dict.fromkeys(request.resource_ids))
        if resource_ids:
            must.append(
                qdrant_models.FieldCondition(
                    key="resource_id",
                    match=qdrant_models.MatchAny(any=list(resource_ids)),
                )
            )
        return qdrant_models.Filter(must=must)


def _to_candidates(
    points: tuple[qdrant_types.ScoredPoint, ...],
) -> tuple[RagRetrievalCandidate, ...]:
    candidates = []
    for rank, point in enumerate(points, start=1):
        if point.payload is None:
            raise RuntimeError("Qdrant RAG candidate payload is missing")
        payload = cast(_CandidatePayload, point.payload)
        candidates.append(
            RagRetrievalCandidate(
                chunk_id=payload["chunk_id"],
                reading_block_id=payload["reading_block_id"],
                section_id=payload["section_id"],
                section_path=tuple(payload["section_path"]),
                resource_id=payload["resource_id"],
                content_revision=payload["content_revision"],
                raw_text=payload["raw_text"],
                anchor_labels=tuple(payload["anchor_labels"]),
                source_ref_id=payload["source_ref_id"],
                signals=(
                    ScoreSignal(
                        candidate_id=payload["chunk_id"],
                        name="qdrant_hybrid_rrf",
                        value=float(point.score),
                        kind=ScoreSignalKind.PRIOR,
                        rank=rank,
                    ),
                ),
            )
        )
    return tuple(candidates)
