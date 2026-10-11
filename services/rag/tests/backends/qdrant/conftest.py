"""Qdrant 驱动 fake 与显式启用的随机隔离 collection，均不读取生产配置。"""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from qdrant_client import AsyncQdrantClient, models

from rag.backends.qdrant import QdrantVectorStorage


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
        await storage.ensure_collection()
        yield storage, client, collection
    finally:
        try:
            if await client.collection_exists(collection):
                await client.delete_collection(collection)
        finally:
            await client.close()
