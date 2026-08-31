"""Qdrant dense/BM25 混合候选召回 adapter。"""

from collections.abc import Mapping, Sequence

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models

from rag.domain import PermissionScope
from rag.domain.models.retrieval import RetrievalCandidate
from rag.domain.repositories.qdrant.candidate_searcher import CandidateSearcher
from common.utils.document import SourceSpan

_PAYLOAD_FIELDS = [
    "content_revision",
    "resource_id",
    "chunk_id",
    "reading_block_id",
    "raw_text",
    "section_id",
    "section_path",
    "source_spans",
    "page_labels",
    "anchor_labels",
    "source_ref_id",
]


class QdrantCandidateSearcher(CandidateSearcher):
    """仅执行 Qdrant 召回和候选 payload 映射，不做应用层排序或核验。"""

    def __init__(
        self,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
        dense_vector_name: str,
        sparse_vector_name: str,
        bm25_options: Mapping[str, object] | None = None,
        dense_vector_size: int | None = None,
    ) -> None:
        if not collection_name.strip():
            raise ValueError("collection_name must not be empty")
        if not dense_vector_name.strip() or not sparse_vector_name.strip():
            raise ValueError("vector names must not be empty")
        if dense_vector_size is not None and dense_vector_size <= 0:
            raise ValueError("dense_vector_size must be positive")
        self._client = client
        self._collection_name = collection_name
        self._dense_vector_name = dense_vector_name
        self._sparse_vector_name = sparse_vector_name
        self._bm25_options = dict(bm25_options or {})
        self._dense_vector_size = dense_vector_size

    async def search(
        self,
        *,
        lexical_query: str,
        semantic_vector: Sequence[float],
        permission_scope: PermissionScope,
        limit: int,
    ) -> list[RetrievalCandidate]:
        if limit <= 0:
            return []
        if not await self._client.collection_exists(self._collection_name):
            return []
        if not lexical_query.strip():
            raise ValueError("lexical_query must not be empty")
        if not semantic_vector:
            raise ValueError("semantic_vector must not be empty")
        if (
            self._dense_vector_size is not None
            and len(semantic_vector) != self._dense_vector_size
        ):
            raise ValueError("semantic_vector size does not match collection")

        query_filter = self._build_filter(permission_scope)
        response = await self._client.query_points(
            collection_name=self._collection_name,
            prefetch=[
                qdrant_models.Prefetch(
                    query=list(semantic_vector),
                    using=self._dense_vector_name,
                    filter=query_filter,
                    limit=limit,
                ),
                qdrant_models.Prefetch(
                    query=qdrant_models.Document(
                        text=lexical_query,
                        model="qdrant/bm25",
                        options=self._bm25_options or None,
                    ),
                    using=self._sparse_vector_name,
                    filter=query_filter,
                    limit=limit,
                ),
            ],
            query=qdrant_models.FusionQuery(fusion=qdrant_models.Fusion.RRF),
            query_filter=query_filter,
            limit=limit,
            with_payload=_PAYLOAD_FIELDS,
        )
        return [
            _to_retrieval_candidate(point.payload, score=point.score)
            for point in response.points
        ]

    @staticmethod
    def _build_filter(permission_scope: PermissionScope) -> qdrant_models.Filter:
        must: list[qdrant_models.Condition] = [
            qdrant_models.FieldCondition(
                key="active",
                match=qdrant_models.MatchValue(value=True),
            ),
            _permission_filter(permission_scope),
        ]
        return qdrant_models.Filter(must=must)



def _to_retrieval_candidate(
    payload: Mapping[str, object] | None,
    *,
    score: float,
) -> RetrievalCandidate:
    if payload is None:
        raise ValueError("Qdrant candidate payload is missing")
    return RetrievalCandidate(
        chunk_id=_required_text(payload, "chunk_id"),
        reading_block_id=_required_text(payload, "reading_block_id"),
        section_id=_required_text(payload, "section_id"),
        section_path=_required_text_list(payload, "section_path"),
        resource_id=_required_text(payload, "resource_id"),
        content_revision=_required_text(payload, "content_revision"),
        raw_text=_required_text(payload, "raw_text"),
        source_spans=_required_spans(payload, "source_spans"),
        page_labels=_required_text_list(payload, "page_labels"),
        anchor_labels=_required_text_list(payload, "anchor_labels"),
        source_ref_id=_required_text(payload, "source_ref_id"),
        score=float(score),
    )


def _required_text(payload: Mapping[str, object], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise TypeError(f"Qdrant candidate payload field {field_name} is invalid")
    return value


def _required_text_list(
    payload: Mapping[str, object],
    field_name: str,
) -> list[str]:
    value = payload.get(field_name)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"Qdrant candidate payload field {field_name} is invalid")
    if not all(isinstance(item, str) for item in value):
        raise TypeError(f"Qdrant candidate payload field {field_name} is invalid")
    return list(value)


def _required_spans(
    payload: Mapping[str, object],
    field_name: str,
) -> list[SourceSpan]:
    value = payload.get(field_name)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"Qdrant candidate payload field {field_name} is invalid")
    spans: list[SourceSpan] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise TypeError(f"Qdrant candidate payload field {field_name} is invalid")
        start_offset = item.get("start_offset")
        end_offset = item.get("end_offset")
        if not isinstance(start_offset, int) or not isinstance(end_offset, int):
            raise TypeError(f"Qdrant candidate payload field {field_name} is invalid")
        spans.append(SourceSpan(start_offset, end_offset))
    if not spans:
        raise ValueError(f"Qdrant candidate payload field {field_name} is empty")
    return spans


def _permission_filter(scope: PermissionScope) -> qdrant_models.Filter:
    """生成与 ResourceAcl.can_read 同语义的 Qdrant VIEW filter。"""
    user_id = scope.user_id
    should: list[qdrant_models.Condition] = [
        qdrant_models.FieldCondition(
            key="owner_id",
            match=qdrant_models.MatchValue(value=user_id),
        ),
        qdrant_models.FieldCondition(
            key="readable_users",
            match=qdrant_models.MatchValue(value=user_id),
        ),
    ]

    group_filters: list[qdrant_models.Condition] = []
    if scope.managed_group_ids:
        group_filters.append(
            _nested_group_filter(
                qdrant_models.Filter(
                    must=[
                        qdrant_models.FieldCondition(
                            key="group_id",
                            match=qdrant_models.MatchAny(
                                any=list(scope.managed_group_ids)
                            ),
                        )
                    ]
                )
            )
        )
    if scope.joined_group_ids:
        joined_ids = list(scope.joined_group_ids)
        group_filters.extend(
            [
                _nested_group_filter(
                    qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key="group_id",
                                match=qdrant_models.MatchAny(any=joined_ids),
                            ),
                            qdrant_models.FieldCondition(
                                key="is_readable",
                                match=qdrant_models.MatchValue(value=True),
                            ),
                        ],
                        must_not=[
                            qdrant_models.FieldCondition(
                                key="excluded_read_users",
                                match=qdrant_models.MatchValue(value=user_id),
                            )
                        ],
                    )
                ),
                _nested_group_filter(
                    qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key="group_id",
                                match=qdrant_models.MatchAny(any=joined_ids),
                            ),
                            qdrant_models.FieldCondition(
                                key="is_readable",
                                match=qdrant_models.MatchValue(value=False),
                            ),
                            qdrant_models.FieldCondition(
                                key="readable_users",
                                match=qdrant_models.MatchValue(value=user_id),
                            ),
                        ]
                    )
                ),
            ]
        )

    if group_filters:
        should.append(
            qdrant_models.Filter(
                must_not=[
                    qdrant_models.FieldCondition(
                        key="excluded_read_users",
                        match=qdrant_models.MatchValue(value=user_id),
                    )
                ],
                should=group_filters,
            )
        )
    return qdrant_models.Filter(should=should)


def _nested_group_filter(group_filter: qdrant_models.Filter) -> qdrant_models.NestedCondition:
    return qdrant_models.NestedCondition(
        nested=qdrant_models.Nested(key="group_acls", filter=group_filter)
    )
