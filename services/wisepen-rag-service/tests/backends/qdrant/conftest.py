"""Qdrant 驱动 fake 与显式启用的随机隔离 collection，均不读取生产配置。"""

import os
from contextlib import AsyncExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models

from rag.backends.qdrant import QdrantVectorStorage
from rag.storage.vector import IndexableField, IndexKind


@pytest.fixture
def completed():
    return models.UpdateResult(operation_id=1, status=models.UpdateStatus.COMPLETED)


@pytest.fixture
def qdrant_client(completed):
    params = models.CollectionParams(
        vectors={"dense": models.VectorParams(size=2, distance=models.Distance.COSINE)},
        sparse_vectors={
            "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
        },
    )
    return SimpleNamespace(
        collection_exists=AsyncMock(return_value=True),
        create_collection=AsyncMock(return_value=True),
        get_collection=AsyncMock(
            return_value=SimpleNamespace(config=SimpleNamespace(params=params))
        ),
        create_payload_index=AsyncMock(return_value=completed),
        upsert=AsyncMock(return_value=completed),
        query_points=AsyncMock(return_value=SimpleNamespace(points=[])),
        delete=AsyncMock(return_value=completed),
    )


@pytest.fixture
def vectors(qdrant_client):
    return QdrantVectorStorage(
        client=qdrant_client, collection="test_vectors", dense_vector_size=2
    )


@pytest.fixture
async def real_vectors():
    url = os.environ.get("RAG_TEST_QDRANT_URL")
    if not url:
        pytest.skip("RAG_TEST_QDRANT_URL is not configured")
    # 远端 Document 转发走服务端 BM25；不在本地模式或 FastEmbed 上假证该能力。
    client = AsyncQdrantClient(
        url=url,
        api_key=os.environ.get("RAG_TEST_QDRANT_API_KEY") or None,
        cloud_inference=True,
        timeout=30,
    )
    collection = f"rag_test_{uuid4().hex}"
    storage = QdrantVectorStorage(
        client=client, collection=collection, dense_vector_size=2
    )
    try:
        await storage.ensure_collection(
            [IndexableField("resource_id", IndexKind.KEYWORD)]
        )
        yield storage, client, collection
    finally:
        try:
            if await client.collection_exists(collection):
                await client.delete_collection(collection)
        finally:
            await client.close()


@pytest.fixture
async def real_graph_vectors():
    url = os.environ.get("RAG_TEST_QDRANT_URL")
    if not url:
        pytest.skip("RAG_TEST_QDRANT_URL is not configured")
    async with AsyncExitStack() as stack:
        client = AsyncQdrantClient(
            url=url,
            api_key=os.environ.get("RAG_TEST_QDRANT_API_KEY") or None,
            timeout=30,
        )
        stack.push_async_callback(client.close)

        async def remove(collection):
            if await client.collection_exists(collection):
                await client.delete_collection(collection)

        stores = []
        for kind in ("entity", "relationship"):
            collection = f"rag_test_graph_{kind}_{uuid4().hex}"
            stack.push_async_callback(remove, collection)
            storage = QdrantVectorStorage(
                client=client,
                collection=collection,
                dense_vector_size=2,
                sparse_vector_name=None,
            )
            await storage.ensure_collection(
                metadata_index_fields=[
                    IndexableField("resource_id", IndexKind.KEYWORD),
                    IndexableField("content_revision", IndexKind.KEYWORD),
                    IndexableField("chunk_id", IndexKind.KEYWORD),
                    IndexableField("ontology_name", IndexKind.KEYWORD),
                ]
            )
            stores.append(storage)
        yield stores[0], stores[1], client
