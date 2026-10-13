"""验证通用条件映射与组合语义，不允许悄悄丢弃无法执行的条件。"""

import pytest
from qdrant_client import QdrantClient, models

from rag.backends.qdrant.filters import compile_filters
from rag.core.schema.filters import (
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)


@pytest.mark.parametrize(
    "operator",
    [FilterOperator.GT, FilterOperator.GTE, FilterOperator.LT, FilterOperator.LTE],
)
def test_numeric_range(operator):
    condition = compile_filters(
        MetadataFilters([MetadataFilter("score", 3.5, operator)])
    )
    assert condition.must[0].key == "metadata.score"
    assert getattr(condition.must[0].range, operator.value) == 3.5


@pytest.mark.parametrize("value", ["revision", 7, True, 1.5])
def test_equality_types(value):
    condition = compile_filters(MetadataFilters([MetadataFilter("value", value)])).must[
        0
    ]
    if type(value) is float:
        assert condition.range.gte == condition.range.lte == value
    else:
        assert condition.match.value == value


@pytest.mark.parametrize("value", [["a", "b"], [1, 2]])
def test_membership(value):
    condition = compile_filters(
        MetadataFilters([MetadataFilter("value", value, FilterOperator.IN)])
    )
    assert condition.must[0].match.any == value


@pytest.mark.parametrize(
    "filters",
    [
        MetadataFilters(),
        MetadataFilters([MetadataFilters()]),
        MetadataFilters(
            [MetadataFilter("x", 1), MetadataFilter("y", 2)], FilterCondition.NOT
        ),
        MetadataFilters([MetadataFilter("", "x")]),
        MetadataFilters([MetadataFilter("x", float("nan"))]),
        MetadataFilters([MetadataFilter("x", [True], FilterOperator.IN)]),
        MetadataFilters([MetadataFilter("x", [], FilterOperator.IN)]),
        MetadataFilters([MetadataFilter("x", [1, "a"], FilterOperator.IN)]),
        MetadataFilters([MetadataFilter("x", True, FilterOperator.GT)]),
        MetadataFilters([MetadataFilter("x", "1", FilterOperator.GTE)]),
        MetadataFilters([MetadataFilter("x", 1, "unknown")]),
        MetadataFilters([MetadataFilter("x", 1)], "unknown"),
    ],
)
def test_invalid_conditions_fail_explicitly(filters):
    with pytest.raises(ValueError):
        compile_filters(filters)


def test_nested_and_or_not_against_qdrant_local_filter_engine():
    # 此处只验证 provider 条件语义；本地模式不能验证服务端 BM25。
    client = QdrantClient(":memory:")
    try:
        client.create_collection(
            "filters",
            vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
        )
        client.upsert(
            "filters",
            [
                models.PointStruct(
                    id=1,
                    vector=[1.0, 0.0],
                    payload={
                        "metadata": {
                            "resource_id": "a",
                            "content_revision": "old",
                            "score": 1.5,
                        }
                    },
                ),
                models.PointStruct(
                    id=2,
                    vector=[0.0, 1.0],
                    payload={
                        "metadata": {
                            "resource_id": "b",
                            "content_revision": "new",
                            "score": 1.5,
                        }
                    },
                ),
                models.PointStruct(
                    id=3,
                    vector=[1.0, 1.0],
                    payload={
                        "metadata": {
                            "resource_id": "c",
                            "content_revision": "new",
                            "score": 2.0,
                        }
                    },
                ),
            ],
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
                    [MetadataFilter("content_revision", "old")], FilterCondition.NOT
                ),
                MetadataFilter("score", 1.5),
            ]
        )
        records, _ = client.scroll("filters", scroll_filter=compile_filters(filters))
        assert [record.id for record in records] == [2]
    finally:
        client.close()
