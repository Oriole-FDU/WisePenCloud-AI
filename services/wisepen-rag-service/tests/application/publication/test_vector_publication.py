"""真实 Markdown、事实与向量 Indexer、Qdrant adapter 在同一发布 Pipeline 中执行。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from qdrant_client import models

from rag.application.publication import DocumentPublication
from rag.domain.models.publication import PublicationAction
from rag.core.persistence.qdrant import QdrantVectorStorage
from rag.domain.models import ContentRevision
from rag.ingestion.chunkers import MarkdownChunker
from rag.ingestion.indexers import ChunkFactsIndexer, VectorIndexer
from rag.ingestion.pipeline import IngestionPipeline
from rag.ingestion.transformers import Contextualizer


@pytest.fixture
def vector_flow(storage, publication_store):
    client = SimpleNamespace(
        upsert=AsyncMock(
            return_value=models.UpdateResult(
                operation_id=1, status=models.UpdateStatus.COMPLETED
            )
        )
    )
    embedding = SimpleNamespace(
        dimensions=2,
        embed=AsyncMock(
            side_effect=lambda **kwargs: [[1.0, 0.0] for _ in kwargs["texts"]]
        ),
    )
    vectors = QdrantVectorStorage(
        client=client, collection="isolated", dense_vector_size=2
    )
    publisher = DocumentPublication(
        store=publication_store,
        reader=storage,
        writer=storage,
        pipeline=IngestionPipeline(
            chunker=MarkdownChunker(),
            transformers=[],
            indexers=[
                ChunkFactsIndexer(writer=storage),
                VectorIndexer(
                    embedding_client=embedding,
                    writer=vectors,
                ),
            ],
        ),
    )
    return publisher, embedding, client


@pytest.mark.parametrize("model_fails", [False, True])
async def test_contextualizer_optional_failure_still_publishes_required_indexes(
        vector_flow, storage, publication_store, make_document, model_fails
):
    publisher, embedding, client = vector_flow
    instruction = SimpleNamespace(
        model="model", complete=AsyncMock(return_value="本段属于文档标题")
    )
    if model_fails:
        instruction.complete.side_effect = OSError("model unavailable")
    publisher._pipeline.transformers.append(Contextualizer(client=instruction))
    document = make_document()
    assert await publisher.publish(document) is PublicationAction.APPLIED
    chunks = await storage.get_revision_chunks(
        resource_id=document.resource_id, content_revision=document.content_revision
    )
    assert all(
        chunk.metadata["context_status"] == ("failed" if model_fails else "ok")
        for chunk in chunks
    )
    assert embedding.embed.await_args.kwargs["texts"] == [
        chunk.retrieval_text for chunk in chunks
    ]
    points = client.upsert.await_args.kwargs["points"]
    assert [point.vector["sparse"].text for point in points] == [
        chunk.retrieval_text for chunk in chunks
    ]
    assert (await publication_store.get_states([document.resource_id]))[
               0
           ].applied_revision.content_revision == document.content_revision


async def test_context_success_does_not_hide_required_vector_failure(
        vector_flow, publication_store, make_document
):
    publisher, _, client = vector_flow
    publisher._pipeline.transformers.append(
        Contextualizer(
            client=SimpleNamespace(
                model="model", complete=AsyncMock(return_value="上下文")
            )
        )
    )
    client.upsert.side_effect = OSError("vector write failed")
    with pytest.raises(OSError):
        await publisher.publish(make_document())
    assert (await publication_store.get_states(["resource"]))[
               0
           ].applied_revision is None


async def test_facts_exist_and_publication_waits_for_both_vectors(
        vector_flow, storage, publication_store, make_document
):
    publisher, _, client = vector_flow
    document = make_document()

    async def upsert(**kwargs):
        facts = await storage.get_revision_chunks(
            resource_id=document.resource_id, content_revision=document.content_revision
        )
        assert [point.payload["record_id"] for point in kwargs["points"]] == [
            chunk.chunk_id for chunk in facts
        ]
        assert all(
            set(point.vector) == {"dense", "sparse"} for point in kwargs["points"]
        )
        assert (await publication_store.get_states(["resource"]))[
                   0
               ].applied_revision is None
        return models.UpdateResult(operation_id=1, status=models.UpdateStatus.COMPLETED)

    client.upsert.side_effect = upsert
    assert await publisher.publish(document) is PublicationAction.APPLIED
    state = (await publication_store.get_states(["resource"]))[0]
    assert state.applied_revision.content_revision == document.content_revision


@pytest.mark.parametrize("failure", ["embedding", "sparse", "acknowledged"])
async def test_vector_failure_keeps_old_applied_and_replay_can_finish(
        vector_flow, storage, publication_store, make_document, failure
):
    publisher, embedding, client = vector_flow
    first, candidate = make_document(), make_document(version=2)
    await publisher.publish(first)
    if failure == "embedding":
        original = embedding.embed.side_effect
        embedding.embed.side_effect = OSError("embedding unavailable")
    elif failure == "sparse":
        client.upsert.side_effect = OSError("BM25 inference unavailable")
    else:
        client.upsert.return_value = models.UpdateResult(
            operation_id=1, status=models.UpdateStatus.ACKNOWLEDGED
        )
    with pytest.raises((RuntimeError, OSError)):
        await publisher.publish(candidate)
    state = (await publication_store.get_states(["resource"]))[0]
    assert state.applied_revision.content_revision == first.content_revision
    assert state.staged_revision.content_revision == candidate.content_revision
    assert await storage.get_revision_chunks(
        resource_id="resource", content_revision=candidate.content_revision
    )
    if failure == "embedding":
        embedding.embed.side_effect = original
    client.upsert.side_effect = None
    client.upsert.return_value = models.UpdateResult(
        operation_id=1, status=models.UpdateStatus.COMPLETED
    )
    assert await publisher.publish(candidate) is PublicationAction.APPLIED
    embedding.embed.reset_mock()
    client.upsert.reset_mock()
    assert await publisher.publish(candidate) is PublicationAction.ALREADY_APPLIED
    embedding.embed.assert_not_awaited()
    client.upsert.assert_not_awaited()


@pytest.mark.parametrize("change", ["newer", "destroyed"])
async def test_state_change_during_vector_write_blocks_apply(
        vector_flow, publication_store, make_document, change
):
    publisher, _, client = vector_flow
    first, candidate = make_document(), make_document(version=2)
    await publisher.publish(first)

    async def upsert(**kwargs):
        if change == "newer":
            await publication_store.stage_revision(
                ContentRevision.create(
                    resource_id="resource", document_version=3, text="新版"
                )
            )
        else:
            await publisher.destroy_resources(["resource"])
        return models.UpdateResult(operation_id=1, status=models.UpdateStatus.COMPLETED)

    client.upsert.side_effect = upsert
    assert await publisher.publish(candidate) is (
        PublicationAction.STALE if change == "newer" else PublicationAction.DESTROYED
    )
    state = (await publication_store.get_states(["resource"]))[0]
    assert (
        state.destroyed
        if change == "destroyed"
        else state.applied_revision.content_revision == first.content_revision
    )


async def test_zero_chunk_publication_has_no_embedding_or_qdrant_io(
        vector_flow, make_document
):
    publisher, embedding, client = vector_flow
    assert await publisher.publish(make_document(text="")) is PublicationAction.APPLIED
    embedding.embed.assert_not_awaited()
    client.upsert.assert_not_awaited()
