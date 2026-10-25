"""仅显式测试配置启用：随机隔离集合上的整数索引、业务过滤及资源删除。"""

import pytest
from qdrant_client import models

from rag.backends.qdrant import QdrantVectorStorage
from rag.core.schema.filters import FilterOperator, MetadataFilter, MetadataFilters
from rag.storage.vector import IndexableField, IndexKind, VectorQuery, VectorRecord

pytestmark = pytest.mark.qdrant_integration


async def test_integer_year_employee_filter_and_resource_cleanup(real_graph_vectors):
    original, _, client = real_graph_vectors
    storage = QdrantVectorStorage(
        client=client,
        collection=original.collection,
        dense_vector_size=2,
        sparse_vector_name=None,
    )
    await storage.ensure_collection(
        [
            IndexableField("resource_id", IndexKind.KEYWORD),
            IndexableField("year", IndexKind.INTEGER),
            IndexableField("employee", IndexKind.KEYWORD),
        ]
    )
    info = await client.get_collection(storage.collection)
    assert (
            info.payload_schema["metadata.year"].data_type
            == models.PayloadSchemaType.INTEGER
    )
    await storage.upsert(
        [
            VectorRecord(
                key,
                "合同",
                [1.0, 0.0],
                {"resource_id": resource, "year": year, "employee": employee},
            )
            for key, resource, year, employee in [
            ("a", "r1", 2020, "李明"),
            ("b", "r2", 2024, "李明"),
            ("c", "r3", 2024, "陈晨"),
        ]
        ]
    )
    filters = MetadataFilters(
        [
            MetadataFilter("year", 2022, FilterOperator.GTE),
            MetadataFilter("employee", "李明"),
        ]
    )
    assert [
               hit.record_id
               for hit in await storage.query(
            VectorQuery(embedding=[1.0, 0.0], filters=filters)
        )
           ] == ["b"]
    await storage.delete(MetadataFilters([MetadataFilter("resource_id", "r2")]))
    assert await storage.query(VectorQuery(embedding=[1.0, 0.0], filters=filters)) == []
    assert len(await storage.query(VectorQuery(embedding=[1.0, 0.0]))) == 2
