"""通过公开向量查询验证嵌套过滤条件的 Qdrant 语义。"""

from qdrant_client import AsyncQdrantClient

from rag.backends.qdrant import QdrantVectorStorage
from rag.core.schema.filters import (
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)
from rag.storage.vector import VectorQuery, VectorRecord


async def test_query_filters_against_qdrant_local_filter_engine():
    # 本地 Qdrant 验证过滤器语义；远端 BM25 行为由集成测试覆盖。
    client = AsyncQdrantClient(location=":memory:")
    try:
        storage = QdrantVectorStorage(
            client=client,
            collection="filters",
            dense_vector_size=2,
            sparse_vector_name=None,
        )
        await storage.ensure_collection([])
        await storage.upsert(
            [
                VectorRecord(
                    record_id="a",
                    text="a",
                    embedding=[1.0, 0.0],
                    metadata={
                        "resource_id": "a",
                        "content_revision": "old",
                        "score": 1.5,
                    },
                ),
                VectorRecord(
                    record_id="b",
                    text="b",
                    embedding=[0.0, 1.0],
                    metadata={
                        "resource_id": "b",
                        "content_revision": "new",
                        "score": 1.5,
                    },
                ),
                VectorRecord(
                    record_id="c",
                    text="c",
                    embedding=[1.0, 1.0],
                    metadata={
                        "resource_id": "c",
                        "content_revision": "new",
                        "score": 2.0,
                    },
                ),
            ]
        )
        filters = MetadataFilters(
            [
                MetadataFilters(
                    [
                        MetadataFilter("resource_id", "a"),
                        MetadataFilter("resource_id", "b"),
                    ],
                    FilterCondition.OR,
                ),
                MetadataFilters(
                    [MetadataFilter("content_revision", "old")],
                    FilterCondition.NOT,
                ),
                MetadataFilter("score", 1.5),
            ]
        )

        matches = await storage.query(
            VectorQuery(embedding=[1.0, 0.0], top_k=3, filters=filters)
        )
        assert [match.record_id for match in matches] == ["b"]
        # 范围和集合查询也验证实际命中，避免只验证编译器生成的 SDK 对象。
        for condition, expected in [
            (MetadataFilter("score", 1.5, FilterOperator.GT), {"c"}),
            (MetadataFilter("score", 1.5, FilterOperator.LTE), {"a", "b"}),
            (MetadataFilter("resource_id", ["a", "c"], FilterOperator.IN), {"a", "c"}),
        ]:
            matches = await storage.query(
                VectorQuery(
                    embedding=[1.0, 0.0],
                    top_k=3,
                    filters=MetadataFilters([condition]),
                )
            )
            assert {match.record_id for match in matches} == expected
    finally:
        await client.close()
