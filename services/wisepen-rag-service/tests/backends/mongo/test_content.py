from dataclasses import asdict, fields, replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from bson.errors import InvalidDocument
from pydantic import ValidationError

from common.utils.markdown import Anchor, Section
from rag.core.schema import Document, DocumentStructure, GraphEdge, GraphNode
from rag.ingestion import IngestionPipeline
from rag.ingestion.chunkers import MarkdownChunker
from rag.ingestion.indexers import ChunkFactsIndexer


async def test_document_and_chunk_bson_round_trip(
        storage, collections, document, chunk
):
    assert await storage.upsert_documents([document]) is None
    assert await storage.upsert_chunks([chunk]) is None

    restored_document = (await storage.get_documents([("resource", "revision")]))[0]
    restored_chunk = (await storage.get_chunks(["chunk"]))[0]

    assert restored_document == document
    assert restored_chunk == chunk
    assert isinstance(restored_document.structure, DocumentStructure)
    assert isinstance(restored_document.structure.sections[0], Section)
    assert isinstance(restored_document.structure.anchors[0], Anchor)
    assert restored_chunk.raw_text == "  中文正文\n"
    assert restored_chunk.retrieval_text == "标题\n\n文档上下文\n\n  中文正文\n"
    documents, chunks = collections
    assert set(documents.records[0]) - {"_id"} == {
        field.name for field in fields(document)
    }
    assert set(chunks.records[0]) - {"_id"} == {field.name for field in fields(chunk)}
    assert "retrieval_text" not in chunks.records[0]
    assert "source_spans" not in chunks.records[0]
    assert "node_ids" not in chunks.records[0]
    assert "retrieval_context" not in chunks.records[0]


async def test_document_type_round_trip_legacy_read_and_positional_metadata(
        storage,
        collections,
        document,
):
    document.type = "history"
    await storage.upsert_documents([document])
    assert (
               await storage.get_documents([(document.resource_id, document.content_revision)])
           )[0] == document
    # 老记录无需迁移；默认值只在模型读取边界补齐。
    collections[0].records[0].pop("type")
    restored = (
        await storage.get_documents([(document.resource_id, document.content_revision)])
    )[0]
    assert restored.type is None and restored.metadata == document.metadata
    positional = Document(
        document.resource_id,
        document.document_version,
        document.content_revision,
        document.text,
        document.structure,
        document.metadata,
    )
    assert positional.metadata is document.metadata and positional.type is None


async def test_graph_metadata_is_plain_data_and_input_is_not_modified(storage, chunk):
    node = GraphNode(name="知识点", category="Concept", source_id=chunk.chunk_id)
    edge = GraphEdge(source_id=node.id, target_id="Concept:目标", relation_type="相关")
    metadata = {"entities": [node], "relationships": [edge]}
    chunk.metadata = metadata

    await storage.upsert_chunks([chunk])
    restored = (await storage.get_chunks([chunk.chunk_id]))[0]

    assert restored.metadata == {
        "entities": [asdict(node)],
        "relationships": [asdict(edge)],
    }
    assert chunk.metadata is metadata
    assert chunk.metadata["entities"][0] is node
    assert chunk.metadata["relationships"][0] is edge


async def test_bson_native_metadata_values_are_preserved(storage, chunk):
    chunk.metadata = {
        "binary": b"\x00\x01",
        "created_at": datetime(2026, 10, 11, tzinfo=UTC),
        "nested": [{"empty": []}],
    }
    await storage.upsert_chunks([chunk])

    restored = (await storage.get_chunks([chunk.chunk_id]))[0]

    assert restored.metadata["binary"] == b"\x00\x01"
    assert isinstance(restored.metadata["created_at"], datetime)
    assert restored.metadata["nested"] == [{"empty": []}]


@pytest.mark.parametrize("kind", ["document", "chunk"])
async def test_empty_content_round_trip(storage, document, chunk, kind):
    if kind == "document":
        value = replace(document, text="", structure=DocumentStructure(0), metadata={})
        await storage.upsert_documents([value])
        assert await storage.get_documents([("resource", "revision")]) == [value]
    else:
        value = replace(
            chunk,
            section_id=None,
            section_path=[],
            raw_text="",
            anchor_labels=[],
            metadata={},
        )
        await storage.upsert_chunks([value])
        assert await storage.get_chunks([value.chunk_id]) == [value]


async def test_upsert_is_idempotent_and_replaces_metadata(
        storage, collections, document, chunk
):
    unrelated = replace(chunk, chunk_id="unrelated", chunk_index=1)
    await storage.upsert_documents([document])
    await storage.upsert_chunks([chunk, unrelated])
    await storage.upsert_documents([document])
    await storage.upsert_chunks([chunk])
    document.metadata = {"new": 1}
    chunk.metadata = {"new": 2}

    await storage.upsert_documents([document])
    await storage.upsert_chunks([chunk])

    documents, chunks = collections
    assert len(documents.records) == 1
    assert len(chunks.records) == 2
    assert (await storage.get_documents([("resource", "revision")]))[0].metadata == {
        "new": 1
    }
    assert (await storage.get_chunks(["chunk"]))[0].metadata == {"new": 2}
    assert await storage.get_chunks(["unrelated"]) == [unrelated]
    documents.create_indexes.assert_not_awaited()
    chunks.create_indexes.assert_not_awaited()


async def test_batch_reads_are_scoped_deduplicated_and_ordered(
        storage, document, chunk
):
    second_document = replace(document, content_revision="second")
    other_document = replace(document, resource_id="other")
    second_chunk = replace(chunk, chunk_id="second", content_revision="second")
    other_chunk = replace(chunk, chunk_id="other", resource_id="other")
    await storage.upsert_documents([document, second_document, other_document])
    await storage.upsert_chunks([chunk, second_chunk, other_chunk])

    assert await storage.get_documents(
        [
            ("other", "revision"),
            ("missing", "revision"),
            ("resource", "second"),
            ("other", "revision"),
            ("resource", "revision"),
        ]
    ) == [other_document, second_document, document]
    assert await storage.get_chunks(
        ["other", "missing", "second", "other", "chunk"]
    ) == [
               other_chunk,
               second_chunk,
               chunk,
           ]


async def test_revision_chunks_are_sorted_and_never_mix_revisions(storage, chunk):
    late = replace(chunk, chunk_id="late", chunk_index=2)
    middle = replace(chunk, chunk_id="middle", chunk_index=1)
    other_revision = replace(chunk, chunk_id="revision-2", content_revision="second")
    other_resource = replace(chunk, chunk_id="other", resource_id="other")
    await storage.upsert_chunks([late, chunk, other_revision, middle, other_resource])

    assert await storage.get_revision_chunks(
        resource_id="resource", content_revision="revision"
    ) == [chunk, middle, late]
    assert (
            await storage.get_revision_chunks(
                resource_id="missing", content_revision="revision"
            )
            == []
    )


async def test_empty_batches_do_not_access_mongo(storage, collections):
    await storage.upsert_documents([])
    await storage.upsert_chunks([])
    assert await storage.get_documents([]) == []
    assert await storage.get_documents_by_section_ids([]) == []
    assert await storage.get_chunks([]) == []
    await storage.delete_revisions([])
    await storage.delete_resources([])

    for collection in collections:
        collection.bulk_write.assert_not_awaited()
        collection.find.assert_not_called()
        collection.delete_many.assert_not_awaited()
        collection.create_indexes.assert_not_awaited()


@pytest.mark.parametrize("kind", ["documents", "chunks"])
async def test_bulk_write_uses_ordered_batches_of_500(
        storage, collections, document, chunk, kind
):
    if kind == "documents":
        values = [
            replace(document, content_revision=f"rev-{index}") for index in range(1001)
        ]
        collection = collections[0]
        await storage.upsert_documents(values)
    else:
        values = [
            replace(chunk, chunk_id=f"chunk-{index}", chunk_index=index)
            for index in range(1001)
        ]
        collection = collections[1]
        await storage.upsert_chunks(values)

    assert [len(call.args[0]) for call in collection.bulk_write.await_args_list] == [
        500,
        500,
        1,
    ]
    assert all(
        call.kwargs == {"ordered": True}
        for call in collection.bulk_write.await_args_list
    )
    assert len(collection.records) == 1001


@pytest.mark.parametrize("kind", ["documents", "chunks"])
async def test_bulk_failure_preserves_completed_batch_and_stops(
        storage, collections, document, chunk, kind
):
    if kind == "documents":
        values = [
            replace(document, content_revision=f"rev-{index}") for index in range(1001)
        ]
        collection = collections[0]
        write = storage.upsert_documents
    else:
        values = [
            replace(chunk, chunk_id=f"chunk-{index}", chunk_index=index)
            for index in range(1001)
        ]
        collection = collections[1]
        write = storage.upsert_chunks
    collection.fail_bulk_call = 2

    with pytest.raises(RuntimeError) as caught:
        await write(values)

    assert caught.value is collection.error
    assert len(collection.records) == 500
    assert collection.bulk_write.await_count == 2
    collection.delete_many.assert_not_awaited()


@pytest.mark.parametrize("kind", ["documents", "chunks"])
async def test_unsupported_bson_values_fail_without_stringification(
        storage, document, chunk, kind
):
    value = document if kind == "documents" else chunk
    unsupported = object()
    value.metadata = {"unsupported": unsupported}
    write = storage.upsert_documents if kind == "documents" else storage.upsert_chunks

    with pytest.raises(InvalidDocument):
        await write([value])

    assert value.metadata["unsupported"] is unsupported


@pytest.mark.parametrize("kind", ["documents", "chunks"])
async def test_invalid_stored_records_fail_at_read_boundary(
        storage, collections, document, chunk, kind
):
    if kind == "documents":
        await storage.upsert_documents([document])
        del collections[0].records[0]["structure"]
        read = storage.get_documents
        identities = [("resource", "revision")]
    else:
        await storage.upsert_chunks([chunk])
        del collections[1].records[0]["raw_text"]
        read = storage.get_chunks
        identities = ["chunk"]

    with pytest.raises(ValidationError):
        await read(identities)


@pytest.mark.parametrize("operation", ["revisions", "resources"])
async def test_delete_is_scoped_to_both_content_collections(
        storage, collections, document, chunk, operation
):
    other_revision_document = replace(document, content_revision="second")
    other_resource_document = replace(document, resource_id="other")
    other_revision_chunk = replace(chunk, chunk_id="second", content_revision="second")
    other_resource_chunk = replace(chunk, chunk_id="other", resource_id="other")
    await storage.upsert_documents(
        [document, other_revision_document, other_resource_document]
    )
    await storage.upsert_chunks([chunk, other_revision_chunk, other_resource_chunk])

    if operation == "revisions":
        await storage.delete_revisions(
            [("resource", "revision"), ("resource", "revision")]
        )
        expected_documents = [other_revision_document, other_resource_document]
        expected_chunks = [other_revision_chunk, other_resource_chunk]
    else:
        await storage.delete_resources(["resource", "resource"])
        expected_documents = [other_resource_document]
        expected_chunks = [other_resource_chunk]

    assert (
            await storage.get_documents(
                [
                    ("resource", "revision"),
                    ("resource", "second"),
                    ("other", "revision"),
                ]
            )
            == expected_documents
    )
    assert await storage.get_chunks(["chunk", "second", "other"]) == expected_chunks
    assert collections[0].delete_many.call_args == collections[1].delete_many.call_args


@pytest.mark.parametrize("operation", ["revisions", "resources"])
async def test_partial_delete_is_preserved_and_can_be_replayed(
        storage, collections, document, chunk, operation
):
    await storage.upsert_documents([document])
    await storage.upsert_chunks([chunk])
    documents, chunks = collections
    error = RuntimeError("Chunk 删除失败")
    original_delete = chunks.delete_many.side_effect
    chunks.delete_many.side_effect = error
    delete = (
        storage.delete_revisions
        if operation == "revisions"
        else storage.delete_resources
    )
    identities = (
        [("resource", "revision")] if operation == "revisions" else ["resource"]
    )

    with pytest.raises(RuntimeError) as caught:
        await delete(identities)

    assert caught.value is error
    assert documents.records == []
    assert len(chunks.records) == 1
    chunks.delete_many.side_effect = original_delete
    await delete(identities)
    assert chunks.records == []


async def test_ensure_indexes_is_explicit_and_defines_content_identity(
        storage, collections
):
    await storage.ensure_indexes()
    documents, chunks = collections
    document_indexes = documents.create_indexes.call_args.args[0]
    chunk_indexes = chunks.create_indexes.call_args.args[0]

    assert [dict(index.document["key"]) for index in document_indexes] == [
        {"resource_id": 1, "content_revision": 1},
        {"structure.sections.section_id": 1},
    ]
    assert [dict(index.document["key"]) for index in chunk_indexes] == [
        {"chunk_id": 1},
        {"resource_id": 1, "content_revision": 1, "chunk_index": 1},
    ]
    assert document_indexes[0].document["unique"]
    assert "unique" not in document_indexes[1].document
    assert all(index.document["unique"] for index in chunk_indexes)


async def test_pipeline_persists_real_markdown_before_projection(storage, document):
    observed = []

    class ProjectionIndexer:
        async def index(self, chunks):
            persisted = await storage.get_revision_chunks(
                resource_id=document.resource_id,
                content_revision=document.content_revision,
            )
            assert persisted == chunks
            observed.extend(persisted)

    await storage.upsert_documents([document])
    pipeline = IngestionPipeline(
        chunker=MarkdownChunker(),
        transformers=[],
        indexers=[ChunkFactsIndexer(storage), ProjectionIndexer()],
    )

    await pipeline.run(document)

    assert observed
    assert await storage.get_documents([("resource", "revision")]) == [document]


async def test_pipeline_content_failure_stops_projection(
        storage, collections, document
):
    collections[1].fail_bulk_call = 1
    projection = AsyncMock()
    pipeline = IngestionPipeline(
        chunker=MarkdownChunker(),
        transformers=[],
        indexers=[ChunkFactsIndexer(storage), projection],
    )

    with pytest.raises(RuntimeError) as caught:
        await pipeline.run(document)

    assert caught.value is collections[1].error
    projection.index.assert_not_awaited()


async def test_pipeline_projection_failure_preserves_content(storage, document):
    error = RuntimeError("投影失败")
    projection = AsyncMock()
    projection.index.side_effect = error
    pipeline = IngestionPipeline(
        chunker=MarkdownChunker(),
        transformers=[],
        indexers=[ChunkFactsIndexer(storage), projection],
    )

    with pytest.raises(RuntimeError) as caught:
        await pipeline.run(document)

    assert caught.value is error
    assert await storage.get_revision_chunks(
        resource_id="resource", content_revision="revision"
    )
