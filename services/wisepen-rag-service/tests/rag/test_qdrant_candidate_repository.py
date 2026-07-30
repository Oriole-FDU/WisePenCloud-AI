from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from qdrant_client import models as qdrant_models

from rag.application.rag.retrieval import (
    RagCandidateRequest,
    RagPermissionFilterBuilder,
    RagPermissionScope,
)
from rag.core.persistence.qdrant.rag_candidate_repository import (
    QdrantRagCandidateRepository,
)
from common.core.domain import GroupRoleType


def _point(
    *,
    point_id: int,
    chunk_id: str,
    score: float,
    content_revision: str = "revision-1",
):
    return qdrant_models.ScoredPoint(
        id=point_id,
        version=1,
        score=score,
        payload={
            "content_revision": content_revision,
            "resource_id": "resource-1",
            "chunk_id": chunk_id,
            "reading_block_id": "block-1",
            "raw_text": f"正文 {chunk_id}",
            "section_id": "section-1",
            "section_path": ["标题"],
            "anchor_labels": [],
            "source_ref_id": "ref-1",
        },
    )


def _client() -> AsyncMock:
    client = AsyncMock()
    client.cloud_inference = True
    return client


@pytest.mark.asyncio
async def test_qdrant_candidate_repository_uses_native_hybrid_query_and_acl_filter() -> (
    None
):
    client = _client()
    client.collection_exists.return_value = True
    client.query_points.return_value = SimpleNamespace(
        points=[_point(point_id=1, chunk_id="chunk-1", score=0.9)]
    )
    repository = QdrantRagCandidateRepository(
        client=client,
        collection_name="rag-test",
        permission_filter_builder=RagPermissionFilterBuilder(),
        bm25_config=qdrant_models.Bm25Config(
            tokenizer=qdrant_models.TokenizerType.MULTILINGUAL
        ),
    )

    candidates = await repository.retrieve_candidates(
        RagCandidateRequest(
            query_text="查询",
            query_vector=[0.1, 0.2],
            permission_scope=RagPermissionScope(
                user_id="user-1",
                group_role_map={"group-1": GroupRoleType.MEMBER},
            ),
            resource_ids=("resource-1",),
            limit=20,
        )
    )

    assert len(candidates) == 1
    assert [signal.name for signal in candidates[0].signals] == ["qdrant_hybrid_rrf"]
    assert candidates[0].source_ref_id == "ref-1"
    assert candidates[0].section_id == "section-1"
    query_call = client.query_points.await_args.kwargs
    assert query_call["query"].fusion is qdrant_models.Fusion.RRF
    dense_prefetch, sparse_prefetch = query_call["prefetch"]
    assert dense_prefetch.using == "dense"
    assert sparse_prefetch.using == "sparse"
    assert sparse_prefetch.query.model == "qdrant/bm25"
    assert len(dense_prefetch.filter.must) == 2
    assert dense_prefetch.filter.must[1].key == "resource_id"
    assert sparse_prefetch.filter == dense_prefetch.filter
    assert "document_version" not in query_call["with_payload"]
    assert "page_labels" not in query_call["with_payload"]


@pytest.mark.asyncio
async def test_qdrant_candidate_repository_returns_empty_without_collection() -> None:
    client = _client()
    client.collection_exists.return_value = False
    repository = QdrantRagCandidateRepository(
        client=client,
        collection_name="rag-test",
        permission_filter_builder=RagPermissionFilterBuilder(),
        bm25_config=qdrant_models.Bm25Config(),
    )

    candidates = await repository.retrieve_candidates(
        RagCandidateRequest(
            query_text="查询",
            query_vector=[],
            permission_scope=RagPermissionScope(
                user_id="user-1",
                group_role_map={},
            ),
        )
    )

    assert candidates == ()
    client.query_points.assert_not_awaited()


@pytest.mark.asyncio
async def test_candidate_repository_preserves_same_chunk_across_revisions() -> None:
    client = _client()
    client.collection_exists.return_value = True
    client.query_points.return_value = SimpleNamespace(
        points=[
            _point(
                point_id=1,
                chunk_id="chunk-1",
                content_revision="revision-staged",
                score=0.9,
            ),
            _point(
                point_id=2,
                chunk_id="chunk-1",
                content_revision="revision-applied",
                score=0.8,
            ),
        ]
    )
    repository = QdrantRagCandidateRepository(
        client=client,
        collection_name="rag-test",
        permission_filter_builder=RagPermissionFilterBuilder(),
        bm25_config=qdrant_models.Bm25Config(),
    )

    candidates = await repository.retrieve_candidates(
        RagCandidateRequest(
            query_text="查询",
            query_vector=[0.1, 0.2],
            permission_scope=RagPermissionScope(
                user_id="user-1",
                group_role_map={},
            ),
        )
    )

    assert {item.content_revision for item in candidates} == {
        "revision-applied",
        "revision-staged",
    }


@pytest.mark.asyncio
async def test_candidate_repository_rejects_missing_payload() -> None:
    client = _client()
    client.collection_exists.return_value = True
    client.query_points.return_value = SimpleNamespace(
        points=[
            qdrant_models.ScoredPoint(
                id=1,
                version=1,
                score=0.9,
                payload=None,
            )
        ]
    )
    repository = QdrantRagCandidateRepository(
        client=client,
        collection_name="rag-test",
        permission_filter_builder=RagPermissionFilterBuilder(),
        bm25_config=qdrant_models.Bm25Config(),
    )

    with pytest.raises(RuntimeError, match="candidate payload is missing"):
        await repository.retrieve_candidates(
            RagCandidateRequest(
                query_text="查询",
                query_vector=[0.1, 0.2],
                permission_scope=RagPermissionScope(
                    user_id="user-1",
                    group_role_map={},
                ),
            )
        )
