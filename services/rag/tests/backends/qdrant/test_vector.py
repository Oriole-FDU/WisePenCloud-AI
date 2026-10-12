"""向量后端转换、显式初始化、写入完成与第三方候选边界。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest
from qdrant_client import AsyncQdrantClient, models

from rag.backends.qdrant import QdrantVectorStorage
from rag.core.schema.filters import MetadataFilter, MetadataFilters
from rag.storage.vector import VectorMatch, VectorQuery, VectorRecord


def record(index=0):
    return VectorRecord(
        f"resource:revision:chunk:{index}",
        "中文检索文本",
        [1.0, 0.0],
        {"resource_id": "resource", "content_revision": "revision"},
    )


@pytest.fixture
def dense_vectors(qdrant_client):
    return QdrantVectorStorage(
        client=qdrant_client,
        collection="graph_entities",
        dense_vector_size=2,
        sparse_vector_name=None,
    )


async def test_dense_only_creation_indexes_upsert_and_query(
    dense_vectors, qdrant_client
):
    qdrant_client.collection_exists.return_value = False
    qdrant_client.get_collection.return_value.config.params.sparse_vectors = None
    await dense_vectors.ensure_collection(
        metadata_index_fields=[
            "resource_id",
            "content_revision",
            "chunk_id",
            "ontology_name",
        ]
    )
    assert (
        qdrant_client.create_collection.await_args.kwargs["sparse_vectors_config"]
        is None
    )
    assert [
        call.kwargs["field_name"]
        for call in qdrant_client.create_payload_index.await_args_list
    ] == [
        "metadata.resource_id",
        "metadata.content_revision",
        "metadata.chunk_id",
        "metadata.ontology_name",
    ]
    await dense_vectors.upsert([record()])
    point = qdrant_client.upsert.await_args.kwargs["points"][0]
    assert point.vector == {"dense": [1.0, 0.0]}
    await dense_vectors.query(VectorQuery(embedding=[1.0, 0.0]))
    assert qdrant_client.query_points.await_args.kwargs["using"] == "dense"


async def test_dense_only_sparse_request_rejected_before_io(
    dense_vectors, qdrant_client
):
    with pytest.raises(ValueError, match="sparse"):
        await dense_vectors.query(VectorQuery(text="任职"))
    qdrant_client.query_points.assert_not_awaited()


@pytest.mark.parametrize("fields", [[""], ["nested.key"]])
async def test_invalid_metadata_index_fields_fail_before_io(
    dense_vectors, qdrant_client, fields
):
    with pytest.raises(ValueError):
        await dense_vectors.ensure_collection(metadata_index_fields=fields)
    qdrant_client.collection_exists.assert_not_awaited()


async def test_dense_configuration_still_checked(dense_vectors, qdrant_client):
    qdrant_client.get_collection.return_value.config.params.vectors["dense"].size = 3
    with pytest.raises(ValueError, match="incompatible"):
        await dense_vectors.ensure_collection()


async def test_empty_upsert_has_no_io(vectors, qdrant_client):
    await vectors.upsert([])
    for method in vars(qdrant_client).values():
        method.assert_not_awaited()


async def test_points_have_stable_id_and_both_vectors_without_text_payload(
    vectors, qdrant_client
):
    item = record()
    await vectors.upsert([item])
    first = qdrant_client.upsert.call_args.kwargs["points"][0]
    await vectors.upsert([item])
    second = qdrant_client.upsert.call_args.kwargs["points"][0]
    assert (
        first.id
        == second.id
        == str(uuid5(NAMESPACE_URL, f"wisepen-rag-v4:vector:{item.record_id}"))
    )
    assert first.vector["dense"] == item.embedding
    assert first.vector["sparse"].text == item.text
    assert first.vector["sparse"].model == "qdrant/bm25"
    assert first.vector["sparse"].options == {"tokenizer": "multilingual"}
    assert first.payload == {"record_id": item.record_id, "metadata": item.metadata}
    assert qdrant_client.upsert.call_args.kwargs["wait"] is True
    qdrant_client.collection_exists.assert_not_awaited()


async def test_upsert_batch_boundary_and_partial_failure(
    vectors, qdrant_client, completed
):
    items = [record(index) for index in range(513)]
    error = OSError("Qdrant unavailable")
    qdrant_client.upsert.side_effect = [completed, error]
    with pytest.raises(OSError) as caught:
        await vectors.upsert(items)
    assert caught.value is error
    assert [
        len(call.kwargs["points"]) for call in qdrant_client.upsert.call_args_list
    ] == [256, 256]
    qdrant_client.upsert.reset_mock(side_effect=True)
    await vectors.upsert(items)
    assert [
        len(call.kwargs["points"]) for call in qdrant_client.upsert.call_args_list
    ] == [256, 256, 1]


@pytest.mark.parametrize("operation", ["upsert", "delete", "index"])
@pytest.mark.parametrize(
    "status", [models.UpdateStatus.ACKNOWLEDGED, models.UpdateStatus.WAIT_TIMEOUT]
)
async def test_only_completed_counts_as_success(
    vectors, qdrant_client, operation, status
):
    method = {
        "upsert": qdrant_client.upsert,
        "delete": qdrant_client.delete,
        "index": qdrant_client.create_payload_index,
    }[operation]
    method.return_value = models.UpdateResult(operation_id=1, status=status)
    with pytest.raises(RuntimeError, match="did not complete"):
        if operation == "upsert":
            await vectors.upsert([record()])
        elif operation == "delete":
            await vectors.delete(
                MetadataFilters([MetadataFilter("resource_id", "resource")])
            )
        else:
            await vectors.ensure_collection()


async def test_initialization_creates_collection_and_indexes_explicitly(
    vectors, qdrant_client
):
    qdrant_client.collection_exists.return_value = False
    await vectors.ensure_collection()
    args = qdrant_client.create_collection.call_args.kwargs
    assert args["collection_name"] == "test_vectors"
    assert args["vectors_config"]["dense"].size == 2
    assert args["vectors_config"]["dense"].distance == models.Distance.COSINE
    assert args["sparse_vectors_config"]["sparse"].modifier == models.Modifier.IDF
    assert [
        call.kwargs["field_name"]
        for call in qdrant_client.create_payload_index.call_args_list
    ] == ["metadata.resource_id", "metadata.content_revision"]


async def test_existing_collection_reinitialization_repairs_partial_indexes(
    vectors, qdrant_client, completed
):
    qdrant_client.create_payload_index.side_effect = [
        completed,
        OSError("index failed"),
    ]
    with pytest.raises(OSError):
        await vectors.ensure_collection()
    qdrant_client.create_payload_index.reset_mock(side_effect=True)
    await vectors.ensure_collection()
    assert qdrant_client.create_payload_index.await_count == 2
    qdrant_client.create_collection.assert_not_awaited()


@pytest.mark.parametrize(
    "change", ["size", "distance", "dense_name", "sparse_name", "idf"]
)
async def test_existing_collection_conflict_does_not_recreate(
    vectors, qdrant_client, change
):
    params = qdrant_client.get_collection.return_value.config.params
    if change == "size":
        params.vectors["dense"].size = 3
    elif change == "distance":
        params.vectors["dense"].distance = models.Distance.DOT
    elif change == "dense_name":
        params.vectors = {}
    elif change == "sparse_name":
        params.sparse_vectors = {}
    else:
        params.sparse_vectors["sparse"].modifier = None
    with pytest.raises(ValueError, match="incompatible"):
        await vectors.ensure_collection()
    qdrant_client.create_collection.assert_not_awaited()
    qdrant_client.create_payload_index.assert_not_awaited()


@pytest.mark.parametrize("dense", [True, False])
async def test_query_preserves_original_ids_scores_and_order(
    vectors, qdrant_client, dense
):
    filters = MetadataFilters([MetadataFilter("content_revision", "revision")])
    qdrant_client.query_points.return_value = SimpleNamespace(
        points=[
            SimpleNamespace(
                payload={
                    "record_id": "chunk-b",
                    "metadata": {"resource_id": "resource"},
                },
                score=0.8,
            ),
            SimpleNamespace(
                payload={"record_id": "chunk-a", "metadata": {}}, score=0.4
            ),
        ]
    )
    request = (
        VectorQuery(embedding=[1.0, 0.0], top_k=2, filters=filters)
        if dense
        else VectorQuery(text="中文", top_k=2, filters=filters)
    )
    assert await vectors.query(request) == [
        VectorMatch("chunk-b", 0.8, {"resource_id": "resource"}),
        VectorMatch("chunk-a", 0.4, {}),
    ]
    args = qdrant_client.query_points.call_args.kwargs
    assert args["using"] == ("dense" if dense else "sparse")
    assert args["limit"] == 2 and args["with_vectors"] is False
    assert args["query_filter"].must[0].key == "metadata.content_revision"
    if not dense:
        assert args["query"].model == "qdrant/bm25"
        assert args["query"].options == {"tokenizer": "multilingual"}


@pytest.mark.parametrize(
    "query",
    [
        VectorQuery(),
        VectorQuery(embedding=[1.0], text="both"),
        VectorQuery(text="中文", top_k=0),
    ],
)
async def test_invalid_query_has_no_io(vectors, qdrant_client, query):
    with pytest.raises(ValueError):
        await vectors.query(query)
    qdrant_client.query_points.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"record_id": "chunk"},
        {"record_id": "", "metadata": {}},
        {"record_id": 1, "metadata": {}},
    ],
)
async def test_bad_provider_payload_is_not_a_valid_match(
    vectors, qdrant_client, payload
):
    qdrant_client.query_points.return_value = SimpleNamespace(
        points=[SimpleNamespace(payload=payload, score=1.0)]
    )
    with pytest.raises(RuntimeError, match="payload"):
        await vectors.query(VectorQuery(text="中文"))


async def test_delete_requires_nonempty_filter_and_waits(vectors, qdrant_client):
    with pytest.raises(ValueError):
        await vectors.delete(MetadataFilters())
    qdrant_client.delete.assert_not_awaited()
    await vectors.delete(MetadataFilters([MetadataFilter("resource_id", "resource")]))
    args = qdrant_client.delete.call_args.kwargs
    assert args["wait"] is True
    assert args["points_selector"].filter.must[0].key == "metadata.resource_id"


async def test_real_client_forwards_bm25_documents_without_local_inference(completed):
    # 使用真正 1.18 客户端，替换网络边界；fake adapter 无法暴露默认本地推理问题。
    client = AsyncQdrantClient(
        url="http://localhost:6333", cloud_inference=True, check_compatibility=False
    )
    try:
        client._client.upsert = AsyncMock(return_value=completed)
        client._client.query_points = AsyncMock(return_value=SimpleNamespace(points=[]))
        vectors = QdrantVectorStorage(
            client=client, collection="test", dense_vector_size=2
        )
        await vectors.upsert([record()])
        assert isinstance(
            client._client.upsert.call_args.kwargs["points"][0].vector["sparse"],
            models.Document,
        )
        await vectors.query(VectorQuery(text="中文"))
        assert isinstance(
            client._client.query_points.call_args.kwargs["query"].nearest,
            models.Document,
        )
    finally:
        await client.close()


@pytest.mark.parametrize("dense", [True, False])
async def test_queries_request_named_dense_vector_for_either_recall_route(
    qdrant_client, dense
):
    vectors = QdrantVectorStorage(
        client=qdrant_client,
        collection="test",
        dense_vector_size=2,
        dense_vector_name="custom_dense",
        sparse_vector_name="custom_sparse",
    )
    qdrant_client.query_points.return_value = SimpleNamespace(
        points=[
            SimpleNamespace(
                payload={"record_id": "chunk", "metadata": {}},
                score=0.7,
                vector={"custom_dense": [1.0, 0.0]},
            )
        ]
    )
    request = (
        VectorQuery(embedding=[1.0, 0.0], include_embedding=True)
        if dense
        else VectorQuery(text="中文", include_embedding=True)
    )
    assert await vectors.query(request) == [VectorMatch("chunk", 0.7, {}, [1.0, 0.0])]
    assert qdrant_client.query_points.call_args.kwargs["with_vectors"] == [
        "custom_dense"
    ]


@pytest.mark.parametrize("dense", [True, False])
@pytest.mark.parametrize(
    "vector",
    [
        None,
        {},
        {"sparse": [1.0, 0.0]},
        [1.0, 0.0],
        {"dense": []},
        {"dense": [1.0]},
        {"dense": [float("nan"), 0.0]},
        {"dense": [float("inf"), 0.0]},
        {"dense": [True, 0.0]},
        {"dense": ["1", 0.0]},
    ],
)
async def test_requested_vector_data_errors_propagate(
    vectors, qdrant_client, dense, vector
):
    qdrant_client.query_points.return_value = SimpleNamespace(
        points=[
            SimpleNamespace(
                payload={"record_id": "chunk", "metadata": {}},
                score=0.7,
                vector=vector,
            )
        ]
    )
    request = (
        VectorQuery(embedding=[1.0, 0.0], include_embedding=True)
        if dense
        else VectorQuery(text="中文", include_embedding=True)
    )
    with pytest.raises(RuntimeError, match="embedding"):
        await vectors.query(request)
