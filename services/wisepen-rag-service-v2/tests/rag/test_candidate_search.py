import pytest
from common.core.domain import GroupRoleType
from qdrant_client import models as qdrant_models
from qdrant_client.http import models as qdrant_http_models

from rag.core.persistence.qdrant import QdrantCandidateSearcher
from rag.domain.models.acl import PermissionScope


class _QdrantClient:
    def __init__(self, *, exists: bool = True) -> None:
        self.exists = exists
        self.request = None
        self.points = []

    async def collection_exists(self, collection_name):
        return self.exists

    async def query_points(self, **kwargs):
        self.request = kwargs
        return qdrant_http_models.QueryResponse(
            points=self.points,
        )


def _search(client: _QdrantClient) -> QdrantCandidateSearcher:
    return QdrantCandidateSearcher(
        client=client,
        collection_name="retrieval-chunks",
        dense_vector_name="dense",
        sparse_vector_name="sparse",
        dense_vector_size=3,
        bm25_options={"tokenizer": "multilingual"},
    )


def _request() -> dict[str, object]:
    return {
        "lexical_query": "关键词",
        "semantic_vector": [0.1, 0.2, 0.3],
        "permission_scope": PermissionScope(user_id="user-1"),
        "limit": 5,
    }


@pytest.mark.asyncio
async def test_search_uses_active_acl_filter_and_hybrid_fusion() -> None:
    client = _QdrantClient()
    client.points = [
        qdrant_models.ScoredPoint(
            id="point-1",
            version=1,
            score=0.75,
            payload={
                "content_revision": "revision-1",
                "resource_id": "resource-1",
                "chunk_id": "chunk-1",
                "reading_block_id": "block-1",
                "raw_text": "原文",
                "section_id": "section-1",
                "section_path": ["标题"],
                "source_spans": [{"start_offset": 0, "end_offset": 2}],
                "page_labels": ["1"],
                "anchor_labels": ["page-1"],
                "source_ref_id": "ref-1",
            },
        )
    ]

    candidates = await _search(client).search(**_request())

    assert candidates[0].chunk_id == "chunk-1"
    assert candidates[0].score == 0.75
    assert candidates[0].source_spans[0].end_offset == 2
    assert client.request["query"].fusion is qdrant_models.Fusion.RRF
    assert client.request["prefetch"][0].using == "dense"
    assert client.request["prefetch"][1].using == "sparse"
    query_filter = client.request["query_filter"]
    assert any(condition.key == "active" for condition in query_filter.must)
    assert not any(
        isinstance(condition, qdrant_models.FieldCondition)
        and condition.key == "resource_id"
        for condition in client.request["prefetch"][0].filter.must
    )


@pytest.mark.asyncio
async def test_search_returns_empty_without_collection_or_limit() -> None:
    client = _QdrantClient(exists=False)
    assert await _search(client).search(**_request()) == []

    client.exists = True
    request = _request()
    request["limit"] = 0
    assert await _search(client).search(**request) == []
    assert client.request is None


@pytest.mark.asyncio
async def test_search_rejects_invalid_query_contract() -> None:
    client = _QdrantClient()
    request = _request()
    request["lexical_query"] = "  "
    with pytest.raises(ValueError, match="lexical_query"):
        await _search(client).search(**request)

    request = _request()
    request["semantic_vector"] = [0.1]
    with pytest.raises(ValueError, match="size"):
        await _search(client).search(**request)


@pytest.mark.asyncio
async def test_search_rejects_missing_candidate_payload() -> None:
    client = _QdrantClient()
    client.points = [
        qdrant_models.ScoredPoint(
            id="point-1",
            version=1,
            score=0.1,
            payload=None,
        )
    ]
    with pytest.raises(ValueError, match="payload is missing"):
        await _search(client).search(**_request())


def test_permission_filter_contains_nested_group_acl_conditions() -> None:
    from rag.core.persistence.qdrant.candidate_searcher import _permission_filter
    from rag.domain.models.acl import PermissionScope

    condition = _permission_filter(
        PermissionScope.from_group_roles(
            "user-1",
            {
                "managed-group": GroupRoleType.ADMIN,
                "joined-group": GroupRoleType.MEMBER,
            },
        )
    )

    group_branch = condition.should[-1]
    nested = group_branch.should
    assert len(nested) == 3
    assert all(isinstance(item, qdrant_models.NestedCondition) for item in nested)
    assert all(item.nested.key == "group_acls" for item in nested)
