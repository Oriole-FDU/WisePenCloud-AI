from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from qdrant_client import models as qdrant_models

from rag.application.rag.acl import (
    RagComputedGroupAclProjection,
    RagResourceAclProjection,
)
from rag.application.rag.ingestion import (
    RagDocumentContent,
    RagSectionProjector,
    prepare_projection_stage,
)
from rag.core.persistence.qdrant import (
    QdrantRagVectorIndexRepository,
    RagVectorIndexError,
)


def _projection():
    return RagSectionProjector().project(
        RagDocumentContent("resource-1", 1, "# 标题\n\n正文。")
    )


def _acl() -> RagResourceAclProjection:
    return RagResourceAclProjection(
        resource_id="resource-1",
        acl_revision=42,
        owner_id="owner-1",
        readable_users=("reader-1",),
        excluded_read_users=("denied-1",),
        computed_group_acls=(
            RagComputedGroupAclProjection(
                group_id="group-1",
                is_readable=True,
                excluded_read_users=("group-denied",),
            ),
        ),
    )


def _repository(client: AsyncMock) -> QdrantRagVectorIndexRepository:
    client.cloud_inference = True
    return QdrantRagVectorIndexRepository(
        client=client,
        collection_name="rag-test",
        dense_vector_size=2,
        embedding_profile="embedding-model",
        bm25_config=qdrant_models.Bm25Config(
            tokenizer=qdrant_models.TokenizerType.MULTILINGUAL
        ),
    )


def test_rejects_client_side_bm25_inference() -> None:
    client = AsyncMock()
    client.cloud_inference = False

    with pytest.raises(ValueError, match="server-side BM25"):
        QdrantRagVectorIndexRepository(
            client=client,
            collection_name="rag-test",
            dense_vector_size=2,
            embedding_profile="embedding-model",
            bm25_config=qdrant_models.Bm25Config(),
        )


@pytest.mark.asyncio
async def test_upserts_revision_scoped_point_with_complete_acl() -> None:
    client = AsyncMock()
    client.collection_exists.return_value = False
    repository = _repository(client)
    projection = _projection()
    stage = prepare_projection_stage(projection, None)

    await repository.upsert_staged_projection(
        projection=projection,
        stage=stage,
        dense_vectors={projection.retrieval_chunks[0].chunk_id: [0.1, 0.2]},
        acl_projection=_acl(),
    )

    client.create_collection.assert_awaited_once()
    points = client.upsert.await_args.kwargs["points"]
    assert len(points) == 1
    payload = points[0].payload
    assert payload["content_revision"] == stage.content_revision
    assert payload["resource_id"] == "resource-1"
    assert payload["acl_revision"] == 42
    assert payload["excluded_read_users"] == ["denied-1"]
    assert payload["computed_group_acls"] == [
        {
            "group_id": "group-1",
            "is_readable": True,
            "readable_users": [],
            "excluded_read_users": ["group-denied"],
        }
    ]
    assert payload["source_ref_id"] == projection.source_refs[0].ref_id
    assert payload["section_id"] == projection.retrieval_chunks[0].section_id
    assert payload["reading_block_id"] == projection.reading_blocks[0].block_id
    assert "document_version" not in payload
    assert "page_labels" not in payload
    assert payload["embedding_key"]
    sparse_document = points[0].vector["sparse"]
    assert sparse_document.model == "qdrant/bm25"
    assert sparse_document.options["tokenizer"] == "multilingual"


@pytest.mark.asyncio
async def test_acl_update_only_targets_same_or_older_revision() -> None:
    client = AsyncMock()
    client.collection_exists.return_value = True
    repository = _repository(client)

    await repository.update_acl_projection(_acl())

    call = client.set_payload.await_args.kwargs
    assert call["payload"]["acl_revision"] == 42
    resource_condition, revision_condition = call["points"].must
    assert resource_condition.key == "resource_id"
    assert resource_condition.match.value == "resource-1"
    assert revision_condition.key == "acl_revision"
    assert revision_condition.range.lte == 42


@pytest.mark.asyncio
async def test_reuses_vector_with_same_embedding_input_across_versions() -> None:
    client = AsyncMock()
    client.collection_exists.return_value = True
    original = _projection()
    projection = RagSectionProjector().project(
        RagDocumentContent("resource-1", 2, "# 标题\n\n正文。")
    )
    repository = _repository(client)
    assert original.retrieval_chunks[0].chunk_id != projection.retrieval_chunks[0].chunk_id
    assert original.retrieval_chunks[0].index_text == projection.retrieval_chunks[0].index_text
    embedding_key = repository._embedding_key(original.retrieval_chunks[0].index_text)
    client.scroll.return_value = (
        [
            qdrant_models.Record(
                id=1,
                payload={"embedding_key": embedding_key},
                vector={"dense": [0.1, 0.2]},
            )
        ],
        None,
    )

    vectors = await repository.load_reusable_vectors(projection)

    assert vectors == {projection.retrieval_chunks[0].chunk_id: [0.1, 0.2]}


@pytest.mark.asyncio
async def test_corrected_content_revision_uses_a_distinct_point_id() -> None:
    client = AsyncMock()
    client.collection_exists.return_value = True
    repository = _repository(client)
    projection = _projection()
    first_stage = prepare_projection_stage(projection, None)
    corrected_stage = replace(first_stage, content_revision="revision-corrected")
    dense_vectors = {projection.retrieval_chunks[0].chunk_id: [0.1, 0.2]}

    await repository.upsert_staged_projection(
        projection=projection,
        stage=first_stage,
        dense_vectors=dense_vectors,
        acl_projection=_acl(),
    )
    await repository.upsert_staged_projection(
        projection=projection,
        stage=corrected_stage,
        dense_vectors=dense_vectors,
        acl_projection=_acl(),
    )

    first_point = client.upsert.await_args_list[0].kwargs["points"][0]
    corrected_point = client.upsert.await_args_list[1].kwargs["points"][0]
    assert first_point.id != corrected_point.id


@pytest.mark.asyncio
async def test_rejects_indexed_content_without_acl() -> None:
    repository = _repository(AsyncMock())
    projection = _projection()

    with pytest.raises(RagVectorIndexError, match="ACL projection"):
        await repository.upsert_staged_projection(
            projection=projection,
            stage=prepare_projection_stage(projection, None),
            dense_vectors={projection.retrieval_chunks[0].chunk_id: [0.1, 0.2]},
            acl_projection=None,
        )


@pytest.mark.asyncio
async def test_rejects_wrong_dense_vector_size() -> None:
    client = AsyncMock()
    client.collection_exists.return_value = True
    repository = _repository(client)
    projection = _projection()

    with pytest.raises(RagVectorIndexError, match="vector size"):
        await repository.upsert_staged_projection(
            projection=projection,
            stage=prepare_projection_stage(projection, None),
            dense_vectors={projection.retrieval_chunks[0].chunk_id: [0.1]},
            acl_projection=_acl(),
        )


@pytest.mark.asyncio
async def test_cleanup_keeps_only_applied_revision() -> None:
    client = AsyncMock()
    client.collection_exists.return_value = True
    repository = _repository(client)

    await repository.delete_other_revisions(
        resource_id="resource-1",
        keep_content_revision="revision-2",
    )

    selector = client.delete.await_args.kwargs["points_selector"]
    assert selector.filter.must[0].key == "resource_id"
    assert selector.filter.must_not[0].key == "content_revision"
    assert selector.filter.must_not[0].match.value == "revision-2"


@pytest.mark.asyncio
async def test_delete_resources_uses_resource_id_filter() -> None:
    client = AsyncMock()
    client.collection_exists.return_value = True
    repository = _repository(client)

    await repository.delete_resources(("resource-1", "resource-2"))

    selector = client.delete.await_args.kwargs["points_selector"]
    condition = selector.filter.must[0]
    assert condition.key == "resource_id"
    assert condition.match.any == ["resource-1", "resource-2"]
