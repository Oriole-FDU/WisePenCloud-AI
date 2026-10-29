"""摄入类别只选择 Pipeline；版本、必需阶段与 Kafka 确认仍由平台拥有。"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from rag.application.publication import ContentIdentityError, DocumentPublication, DocumentReadyEvent
from rag.domain.models.publication import PublicationAction
from rag.ingestion import IngestionPipeline
from rag.ingestion.chunkers import MarkdownChunker
from rag.ingestion.indexers import ChunkFactsIndexer, GraphIndexer, VectorIndexer


def test_event_transmits_open_type_and_business_metadata_without_changing_revision():
    payload = {"resourceId": "r", "version": 1, "content": "# 文献\n\n原文"}
    legacy = DocumentReadyEvent.model_validate(payload).to_document()
    typed = DocumentReadyEvent.model_validate(
        payload
        | {
            "type": "历史资料:v2",
            "metadata": {"year": 1850, "catalog": {"tags": ["历史"]}},
            "other": "ignored",
        }
    ).to_document()
    assert legacy.type is None and legacy.metadata == {}
    assert typed.type == "历史资料:v2"
    assert typed.metadata == {"year": 1850, "catalog": {"tags": ["历史"]}}
    assert typed.content_revision == legacy.content_revision
    assert typed.text == legacy.text and typed.structure == legacy.structure


@pytest.mark.parametrize(
    "change",
    [
        {"type": 1},
        {"type": False},
        {"type": []},
        {"metadata": None},
        {"metadata": []},
        {"metadata": "year=1850"},
    ],
)
def test_event_rejects_invalid_external_types(change):
    with pytest.raises(ValidationError):
        DocumentReadyEvent.model_validate(
            {"resourceId": "r", "version": 1, "content": "text"} | change
        )


async def test_multiple_registered_routes_and_default_use_the_same_publisher(
        storage,
        publication_store,
        make_document,
):
    projections = {
        key: SimpleNamespace(index=AsyncMock()) for key in (None, "history", "science")
    }
    pipelines = {
        key: IngestionPipeline(
            chunker=MarkdownChunker(),
            transformers=[],
            indexers=[ChunkFactsIndexer(storage), projection],
        )
        for key, projection in projections.items()
    }
    routes = {}
    publisher = DocumentPublication(
        store=publication_store,
        reader=storage,
        writer=storage,
        pipeline=pipelines[None],
        pipelines=routes,
    )
    # 构造后注册必须生效，空字典不能被替换为另一个隐式副本。
    routes.update(history=pipelines["history"], science=pipelines["science"])
    for key in (None, "history", "science"):
        document = replace(
            make_document(resource_id=key or "default", metadata={"year": 1850}),
            type=key,
        )
        assert await publisher.publish(document) is PublicationAction.APPLIED
        restored = (
            await storage.get_documents(
                [(document.resource_id, document.content_revision)]
            )
        )[0]
        assert restored.type == key and restored.metadata == document.metadata
        chunks = await storage.get_revision_chunks(
            resource_id=document.resource_id, content_revision=document.content_revision
        )
        assert all(chunk.metadata == {"year": 1850} for chunk in chunks)
    for projection in projections.values():
        projection.index.assert_awaited_once()


@pytest.mark.parametrize("type_name", ["missing", "History", " history", "", " "])
async def test_unknown_type_is_exact_and_fails_before_any_io(make_document, type_name):
    store, content = (
        SimpleNamespace(stage_revision=AsyncMock()),
        SimpleNamespace(upsert_documents=AsyncMock()),
    )
    pipeline = SimpleNamespace(run=AsyncMock())
    publisher = DocumentPublication(
        store=store,
        reader=content,
        writer=content,
        pipeline=pipeline,
        pipelines={"history": pipeline},
    )
    with pytest.raises(ValueError, match="unregistered document type"):
        await publisher.publish(replace(make_document(), type=type_name))
    store.stage_revision.assert_not_awaited()
    content.upsert_documents.assert_not_awaited()
    pipeline.run.assert_not_awaited()


async def test_identity_check_precedes_type_route(make_document):
    publisher = DocumentPublication(
        store=object(), reader=object(), writer=object(), pipeline=object()
    )
    with pytest.raises(ContentIdentityError):
        await publisher.publish(
            replace(make_document(), type="missing", content_revision="wrong")
        )


async def test_route_is_selected_once_before_stage(
        storage, publication_store, make_document
):
    first, replacement = (
        SimpleNamespace(index=AsyncMock()),
        SimpleNamespace(index=AsyncMock()),
    )

    def pipeline(projection):
        return IngestionPipeline(
            chunker=MarkdownChunker(),
            transformers=[],
            indexers=[ChunkFactsIndexer(storage), projection],
        )

    routes = {"history": pipeline(first)}
    original_stage = publication_store.stage_revision

    async def stage(revision):
        routes["history"] = pipeline(replacement)
        return await original_stage(revision)

    publication_store.stage_revision = stage
    publisher = DocumentPublication(
        store=publication_store,
        reader=storage,
        writer=storage,
        pipeline=object(),
        pipelines=routes,
    )
    assert (
            await publisher.publish(replace(make_document(), type="history"))
            is PublicationAction.APPLIED
    )
    first.index.assert_awaited_once()
    replacement.index.assert_not_awaited()


async def test_type_and_metadata_require_new_version_and_destroyed_still_blocks_publish(
        storage,
        publication_store,
        make_document,
):
    first, second = (
        SimpleNamespace(index=AsyncMock()),
        SimpleNamespace(index=AsyncMock()),
    )

    def pipeline(projection):
        return IngestionPipeline(
            chunker=MarkdownChunker(),
            transformers=[],
            indexers=[ChunkFactsIndexer(storage), projection],
        )

    routes = {"history": pipeline(first), "science": pipeline(second)}
    publisher = DocumentPublication(
        store=publication_store,
        reader=storage,
        writer=storage,
        pipeline=object(),
        pipelines=routes,
    )
    original = replace(make_document(metadata={"year": 1850}), type="history")
    assert await publisher.publish(original) is PublicationAction.APPLIED
    changed = replace(original, type="science", metadata={"year": 1900})
    assert await publisher.publish(changed) is PublicationAction.ALREADY_APPLIED
    second.index.assert_not_awaited()
    assert (
               await storage.get_documents([(original.resource_id, original.content_revision)])
           )[0] == original
    # 替换路由也不会自动重建相同已发布 revision。
    routes["history"] = pipeline(second)
    assert await publisher.publish(original) is PublicationAction.ALREADY_APPLIED
    second.index.assert_not_awaited()
    newer = replace(make_document(version=2, metadata=changed.metadata), type="science")
    assert await publisher.publish(newer) is PublicationAction.APPLIED
    second.index.assert_awaited_once()
    assert await publisher.publish(original) is PublicationAction.STALE
    await publisher.destroy_resources([original.resource_id])
    assert (
            await publisher.publish(replace(make_document(version=3), type="history"))
            is PublicationAction.DESTROYED
    )
    assert first.index.await_count == 1 and second.index.await_count == 1


async def test_registered_graph_failure_keeps_old_applied_and_saved_metadata(
        storage,
        publication_store,
        make_document,
        graph_ontology,
        make_graph_facts,
):
    vectors = SimpleNamespace(upsert=AsyncMock())
    embedding = SimpleNamespace(
        dimensions=2,
        embed=AsyncMock(side_effect=lambda *, texts: [[1.0, 0.0] for _ in texts]),
    )
    writer = SimpleNamespace(
        replace_sources=AsyncMock(side_effect=OSError("graph unavailable"))
    )

    async def transform(chunks, *, document):
        for chunk in chunks:
            make_graph_facts(chunk)
            chunk.metadata["contextual_context"] = "上下文"
        return chunks

    default = IngestionPipeline(
        chunker=MarkdownChunker(),
        transformers=[],
        indexers=[ChunkFactsIndexer(storage)],
    )
    registered = IngestionPipeline(
        chunker=MarkdownChunker(),
        transformers=[SimpleNamespace(transform=transform)],
        indexers=[
            ChunkFactsIndexer(storage),
            VectorIndexer(embedding_client=embedding, writer=vectors),
            GraphIndexer(ontology=graph_ontology, writer=writer),
        ],
    )
    publisher = DocumentPublication(
        store=publication_store,
        reader=storage,
        writer=storage,
        pipeline=default,
        pipelines={"history": registered},
    )
    original = make_document()
    assert await publisher.publish(original) is PublicationAction.APPLIED
    candidate = replace(
        make_document(version=2, metadata={"catalog": {"year": 1850}}), type="history"
    )
    with pytest.raises(OSError, match="graph unavailable"):
        await publisher.publish(candidate)
    state = (await publication_store.get_states([candidate.resource_id]))[0]
    assert state.applied_revision.content_revision == original.content_revision
    assert state.staged_revision.content_revision == candidate.content_revision
    chunks = await storage.get_revision_chunks(
        resource_id=candidate.resource_id, content_revision=candidate.content_revision
    )
    assert chunks[0].metadata["catalog"] == {"year": 1850}
    assert chunks[0].metadata["contextual_context"] == "上下文"
    assert chunks[0].metadata["entities"] and chunks[0].metadata["relationships"]
    vectors.upsert.assert_awaited_once()
    assert all(
        record.metadata
        == {
            "resource_id": candidate.resource_id,
            "content_revision": candidate.content_revision,
        }
        for record in vectors.upsert.call_args.args[0]
    )
    writer.replace_sources.assert_awaited_once()
