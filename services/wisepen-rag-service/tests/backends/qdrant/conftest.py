"""Qdrant 驱动 fake 与显式启用的随机隔离 collection，均不读取生产配置。"""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient, models

from rag.core.persistence.qdrant import QdrantVectorStorage
from rag.domain.storage.vector import IndexableField, IndexKind


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


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def qdrant_integration_collections():
    url = os.environ.get("RAG_TEST_QDRANT_URL")
    if not url:
        pytest.skip("RAG_TEST_QDRANT_URL is not configured")

    collections = {
        "vectors": f"rag_test_{uuid4().hex}",
        "entities": f"rag_test_graph_entity_{uuid4().hex}",
        "relationships": f"rag_test_graph_relationship_{uuid4().hex}",
    }
    client = AsyncQdrantClient(
        url=url,
        api_key=os.environ.get("RAG_TEST_QDRANT_API_KEY") or None,
        cloud_inference=True,
        timeout=30,
    )
    try:
        await QdrantVectorStorage(
            client=client,
            collection=collections["vectors"],
            dense_vector_size=2,
        ).ensure_collection(
            [IndexableField("resource_id", IndexKind.KEYWORD)]
        )
        for collection in (collections["entities"], collections["relationships"]):
            await QdrantVectorStorage(
                client=client,
                collection=collection,
                dense_vector_size=2,
                sparse_vector_name=None,
            ).ensure_collection(
                metadata_index_fields=[
                    IndexableField("resource_id", IndexKind.KEYWORD),
                    IndexableField("content_revision", IndexKind.KEYWORD),
                    IndexableField("chunk_id", IndexKind.KEYWORD),
                    IndexableField("ontology_name", IndexKind.KEYWORD),
                ]
            )
        # 仅 session 循环负责集合生命周期；测试循环只连接并清理 points。
        yield collections
    finally:
        try:
            for collection in collections.values():
                if await client.collection_exists(collection):
                    await client.delete_collection(collection)
        finally:
            await client.close()


@pytest.fixture
async def real_vectors(qdrant_integration_collections):
    url = os.environ["RAG_TEST_QDRANT_URL"]
    collection = qdrant_integration_collections["vectors"]
    client = AsyncQdrantClient(
        url=url,
        api_key=os.environ.get("RAG_TEST_QDRANT_API_KEY") or None,
        cloud_inference=True,
        timeout=30,
    )
    storage = QdrantVectorStorage(
        client=client, collection=collection, dense_vector_size=2
    )
    try:
        yield storage, client, collection
    finally:
        try:
            result = await client.delete(
                collection_name=collection,
                points_selector=models.FilterSelector(filter=models.Filter()),
                wait=True,
            )
            assert result.status == models.UpdateStatus.COMPLETED
        finally:
            await client.close()


@pytest.fixture
async def real_graph_vectors(qdrant_integration_collections):
    url = os.environ["RAG_TEST_QDRANT_URL"]
    client = AsyncQdrantClient(
        url=url,
        api_key=os.environ.get("RAG_TEST_QDRANT_API_KEY") or None,
        cloud_inference=True,
        timeout=30,
    )
    stores = [
        QdrantVectorStorage(
            client=client,
            collection=qdrant_integration_collections[kind],
            dense_vector_size=2,
            sparse_vector_name=None,
        )
        for kind in ("entities", "relationships")
    ]
    try:
        yield stores[0], stores[1], client
    finally:
        try:
            for kind in ("entities", "relationships"):
                result = await client.delete(
                    collection_name=qdrant_integration_collections[kind],
                    points_selector=models.FilterSelector(filter=models.Filter()),
                    wait=True,
                )
                assert result.status == models.UpdateStatus.COMPLETED
        finally:
            await client.close()
