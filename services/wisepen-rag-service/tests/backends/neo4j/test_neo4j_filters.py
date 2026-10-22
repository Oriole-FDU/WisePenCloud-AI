"""通过公开图读取验证嵌套过滤与 Cypher 参数隔离。"""

from rag.backends.neo4j import Neo4jGraphStorage
from rag.core.schema import GraphNode
from rag.core.schema.filters import FilterCondition, MetadataFilter, MetadataFilters
from rag.storage.graph import GraphSourceProjection


async def test_nested_filters_and_parameter_safety(driver):
    storage = Neo4jGraphStorage(driver=driver)
    matching = GraphNode("Alice", "matching", "work", "Person")
    other = GraphNode("Bob", "other", "work", "Person")
    await storage.replace_sources(
        [
            GraphSourceProjection("matching", {"resource_id": "b"}, nodes=[matching]),
            GraphSourceProjection("other", {"resource_id": "c"}, nodes=[other]),
        ]
    )

    key = "unsafe'] RETURN 1 //"
    value = "' OR true //"
    filters = MetadataFilters(
        [
            MetadataFilters(
                [MetadataFilter(key, value)], condition=FilterCondition.NOT
            ),
            MetadataFilters(
                [
                    MetadataFilter("resource_id", "b"),
                    MetadataFilter("resource_id", "a"),
                ],
                condition=FilterCondition.OR,
            ),
        ]
    )

    # 缺失属性先按 false 处理，再由 NOT 取反；用户提供的键和值仍作参数传递。
    assert await storage.get_nodes([matching.id, other.id], filters=filters) == [
        matching
    ]
    _, query, parameters = driver.calls[-1]
    assert key not in query and value not in query
    assert parameters["key_0"] == f"meta_{key}"
    assert parameters["value_0"] == value
