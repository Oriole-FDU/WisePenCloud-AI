"""显式提供 RAG_TEST_MONGODB_URI 时，在本次测试专用数据库验证真实 Mongo。"""

import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import TypeAdapter
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from rag.backends.mongo.content import MongoContentStorage
from rag.core.schema import Chunk
from rag.ingestion.indexers import ChunkFactsIndexer
from rag.ingestion.transformers import GraphExtractor
from rag.ingestion.transformers.graph import GraphExtraction

pytestmark = pytest.mark.mongo_integration


async def test_real_mongo_extracted_graph_facts_round_trip(
    mongo_storage, chunk, graph_ontology
):
    storage, _, _ = mongo_storage
    client = SimpleNamespace(
        model="model",
        structured_complete=AsyncMock(
            return_value=GraphExtraction.model_validate(
                {
                    "entities": [
                        {"name": "张三", "category": "Person"},
                        {"name": "甲公司", "category": "Company"},
                    ],
                    "relationships": [
                        {
                            "source": {"name": "张三", "category": "Person"},
                            "target": {"name": "甲公司", "category": "Company"},
                            "relation_type": "works_at",
                        }
                    ],
                }
            )
        ),
    )
    extractor = GraphExtractor(ontology=graph_ontology, client=client)
    await extractor.transform([chunk])
    await ChunkFactsIndexer(storage).index([chunk])
    restored = (await storage.get_chunks([chunk.chunk_id]))[0]
    assert restored == chunk
    assert isinstance(restored.metadata["entities"][0], dict)
    await extractor.transform([restored])
    assert client.structured_complete.await_count == 1


@pytest_asyncio.fixture
async def mongo_storage():
    uri = os.environ.get("RAG_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("未配置 RAG_TEST_MONGODB_URI，跳过真实 Mongo 集成测试")

    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
    database_name = f"codex_rag_content_test_{uuid4().hex}"
    database = client[database_name]
    documents = database["document_revisions"]
    chunks = database["doc_chunks"]
    storage = MongoContentStorage(documents=documents, chunks=chunks)
    try:
        await storage.ensure_indexes()
        yield storage, documents, chunks
    finally:
        try:
            # 仅删除本次 fixture 创建的随机测试库，不读取或清理生产数据库名。
            await client.drop_database(database_name)
        finally:
            await client.close()


async def test_real_mongo_round_trip_idempotency_and_unique_indexes(
    mongo_storage, document, chunk
):
    storage, documents, chunks = mongo_storage
    await storage.ensure_indexes()
    await storage.upsert_documents([document])
    await storage.upsert_chunks([chunk])
    await storage.upsert_documents([document])
    await storage.upsert_chunks([chunk])

    assert await documents.count_documents({}) == 1
    assert await chunks.count_documents({}) == 1
    assert await storage.get_documents([("resource", "revision")]) == [document]
    assert await storage.get_documents_by_section_ids(
        [
            document.structure.sections[0].section_id,
            "missing",
            document.structure.sections[0].section_id,
        ]
    ) == [document]
    indexes = await documents.index_information()
    assert indexes["document_section_id"]["key"] == [
        ("structure.sections.section_id", 1)
    ]
    assert await storage.get_chunks(["chunk", "missing", "chunk"]) == [chunk]
    assert await storage.get_revision_chunks(
        resource_id="resource", content_revision="revision"
    ) == [chunk]

    # 另一个 chunk_id 不能占用同一 revision 的同一个 chunk_index。
    conflicting = replace(chunk, chunk_id="conflicting")
    with pytest.raises(DuplicateKeyError):
        await chunks.insert_one(
            TypeAdapter(Chunk).dump_python(conflicting, mode="python")
        )
    with pytest.raises(DuplicateKeyError):
        await chunks.insert_one(TypeAdapter(Chunk).dump_python(chunk, mode="python"))
    document_record = await documents.find_one({})
    document_record.pop("_id")
    with pytest.raises(DuplicateKeyError):
        await documents.insert_one(document_record)


@pytest.mark.parametrize("operation", ["revisions", "resources"])
async def test_real_mongo_deletion_is_scoped(mongo_storage, document, chunk, operation):
    storage, documents, chunks = mongo_storage
    other_document = replace(document, resource_id="other")
    other_chunk = replace(chunk, chunk_id="other", resource_id="other")
    await storage.upsert_documents([document, other_document])
    await storage.upsert_chunks([chunk, other_chunk])

    if operation == "revisions":
        await storage.delete_revisions([("resource", "revision")])
    else:
        await storage.delete_resources(["resource"])

    assert await documents.count_documents({}) == 1
    assert await chunks.count_documents({}) == 1
    assert await storage.get_documents(
        [("resource", "revision"), ("other", "revision")]
    ) == [other_document]
    assert await storage.get_chunks(["chunk", "other"]) == [other_chunk]
