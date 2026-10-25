"""真实 Qdrant 服务端 BM25 与 dense 行为；只使用随机隔离 collection。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest

from rag.application.gc import GcReport, ResourceGarbageCollector
from rag.application.publication import DocumentPublication, PublicationAction
from rag.core.schema import ContentRevision, Document, DocumentStructure
from rag.core.schema.filters import FilterOperator, MetadataFilter, MetadataFilters
from rag.ingestion.chunkers import MarkdownChunker
from rag.ingestion.indexers import ChunkFactsIndexer, VectorIndexer
from rag.ingestion.pipeline import IngestionPipeline
from rag.storage.vector import VectorQuery, VectorRecord

pytestmark = pytest.mark.qdrant_integration


async def test_real_gc_clears_all_vector_collections_by_resource(
        real_vectors, real_graph_vectors
):
    normal, _, _ = real_vectors
    entities, relationships, _ = real_graph_vectors
    for storage in (normal, entities, relationships):
        await storage.upsert(
            [
                VectorRecord(
                    f"{resource}-{revision}",
                    "正文",
                    [1.0, 0.0],
                    {"resource_id": resource, "content_revision": revision},
                )
                for resource, revision in (
                ("gone", "old"),
                ("gone", "current"),
                ("live", "current"),
            )
            ]
        )
    store = SimpleNamespace(
        list_destroyed_resource_ids=AsyncMock(side_effect=[["gone"], []])
    )
    gc = ResourceGarbageCollector(
        store=store,
        content_writer=SimpleNamespace(delete_resources=AsyncMock()),
        resource_acls=SimpleNamespace(delete=AsyncMock()),
        vector_writers=[normal, entities, relationships],
        graph_writers=[],
    )
    assert await gc.collect() == GcReport(1, 1, 0)
    for storage in (normal, entities, relationships):
        assert [
                   match.record_id
                   for match in await storage.query(VectorQuery(embedding=[1.0, 0.0]))
               ] == ["live-current"]


async def test_real_dense_bm25_revision_filters_and_delete(real_vectors):
    vectors, client, collection = real_vectors
    await vectors.upsert(
        [
            VectorRecord(
                "physics-old",
                "量子 物理 实验",
                [1.0, 0.0],
                {"resource_id": "physics", "content_revision": "old"},
            ),
            VectorRecord(
                "physics-new",
                "量子 物理 实验",
                [1.0, 0.0],
                {"resource_id": "physics", "content_revision": "new"},
            ),
            VectorRecord(
                "fruit",
                "苹果 香蕉 水果",
                [0.0, 1.0],
                {"resource_id": "fruit", "content_revision": "new"},
            ),
        ]
    )
    filters = MetadataFilters(
        [
            MetadataFilter("resource_id", "physics"),
            MetadataFilter("content_revision", "new"),
        ]
    )
    assert [
               match.record_id
               for match in await vectors.query(
            VectorQuery(embedding=[1.0, 0.0], filters=filters)
        )
           ] == ["physics-new"]
    assert [
               match.record_id
               for match in await vectors.query(VectorQuery(text="量子", filters=filters))
           ] == ["physics-new"]
    for request in (
            VectorQuery(embedding=[1.0, 0.0], filters=filters, include_embedding=True),
            VectorQuery(text="量子", filters=filters, include_embedding=True),
    ):
        matches = await vectors.query(request)
        assert [match.record_id for match in matches] == ["physics-new"]
        assert matches[0].embedding == [1.0, 0.0]
    await vectors.delete(
        MetadataFilters([MetadataFilter("resource_id", ["physics"], FilterOperator.IN)])
    )
    assert (await client.count(collection, exact=True)).count == 1
    assert await vectors.query(VectorQuery(text="量子", filters=filters)) == []


async def test_real_replay_replaces_payload_and_preserves_one_point(real_vectors):
    vectors, client, collection = real_vectors
    first = VectorRecord(
        "original:chunk:id",
        "原检索文本",
        [1.0, 0.0],
        {"resource_id": "resource", "content_revision": "rev", "removed": True},
    )
    second = VectorRecord(
        first.record_id,
        "新检索文本",
        [0.0, 1.0],
        {"resource_id": "resource", "content_revision": "rev"},
    )
    await vectors.upsert([first])
    await vectors.upsert([second])
    assert (await client.count(collection, exact=True)).count == 1
    points = await client.retrieve(
        collection,
        ids=[str(uuid5(NAMESPACE_URL, f"wisepen-rag-v4:vector:{first.record_id}"))],
        with_payload=True,
        with_vectors=True,
    )
    assert points[0].payload == {
        "record_id": second.record_id,
        "metadata": second.metadata,
    }
    assert set(points[0].vector) == {"dense", "sparse"}
    assert points[0].vector["dense"] == [0.0, 1.0]


async def test_publication_pipeline_with_real_qdrant(
        real_vectors, storage, publication_store
):
    vectors, client, collection = real_vectors
    text = "# 物理\n\n量子 物理 实验\n"
    revision = ContentRevision.create(
        resource_id="physics", document_version=1, text=text
    )
    document = Document(
        "physics", 1, revision.content_revision, text, DocumentStructure(len(text))
    )
    # 此测试验证真实向量写入与发布闭环，embedding 和 Mongo 使用显式测试替身。
    embedding = SimpleNamespace(
        dimensions=2,
        embed=AsyncMock(
            side_effect=lambda **kwargs: [[1.0, 0.0] for _ in kwargs["texts"]]
        ),
    )
    publisher = DocumentPublication(
        store=publication_store,
        reader=storage,
        writer=storage,
        pipeline=IngestionPipeline(
            chunker=MarkdownChunker(),
            transformers=[],
            indexers=[
                ChunkFactsIndexer(storage),
                VectorIndexer(
                    embedding_client=embedding,
                    writer=vectors,
                ),
            ],
        ),
    )
    assert await publisher.publish(document) is PublicationAction.APPLIED
    chunks = await storage.get_revision_chunks(
        resource_id="physics", content_revision=revision.content_revision
    )
    filters = MetadataFilters(
        [
            MetadataFilter("resource_id", "physics"),
            MetadataFilter("content_revision", revision.content_revision),
        ]
    )
    assert {
               match.record_id
               for match in await vectors.query(VectorQuery(text="量子", filters=filters))
           } == {chunk.chunk_id for chunk in chunks}
    assert (await client.count(collection, exact=True)).count == len(chunks)
    assert await publisher.publish(document) is PublicationAction.ALREADY_APPLIED
    assert embedding.embed.await_count == 1
