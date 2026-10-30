"""仅显式提供测试连接时执行；随机 graph_name 隔离图数据及约束。"""

import os
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from rag.application.gc import GcReport, ResourceGarbageCollector
from rag.core.persistence.neo4j import Neo4jGraphStorage
from rag.domain.models import GraphEdge, GraphNode
from rag.core.filters import MetadataFilter, MetadataFilters
from rag.domain.storage.graph import GraphDirection, GraphSourceProjection

pytestmark = pytest.mark.neo4j_integration


@pytest_asyncio.fixture
async def real_storage():
    uri = os.getenv("RAG_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("未配置 RAG_TEST_NEO4J_URI，跳过真实 Neo4j 集成测试")
    username = os.getenv("RAG_TEST_NEO4J_USERNAME")
    password = os.getenv("RAG_TEST_NEO4J_PASSWORD")
    auth = (username, password) if username else None
    driver = AsyncGraphDatabase.driver(uri, auth=auth, connection_timeout=5)
    database = os.getenv("RAG_TEST_NEO4J_DATABASE", "neo4j")
    graph_name = f"codex_test_{uuid4().hex}"
    storage = Neo4jGraphStorage(
        driver=driver, database=database, graph_name=graph_name, batch_size=1
    )
    try:
        await storage.ensure_schema()
        yield storage, driver, database, graph_name
    finally:
        try:
            async with driver.session(database=database) as session:
                # 仅匹配本 fixture 自己的标签，并删除同前缀约束/索引。
                result = await session.run(
                    "MATCH (n) WHERE any(label IN labels(n) WHERE label IN $labels) DETACH DELETE n",
                    labels=[
                        f"{graph_name}_{kind}"
                        for kind in ("Entity", "Relation", "Source")
                    ],
                )
                await result.consume()
                for suffix in ("entity_id", "relation_id", "source_id"):
                    result = await session.run(
                        f"DROP CONSTRAINT {graph_name}_{suffix} IF EXISTS"
                    )
                    await result.consume()
                result = await session.run(
                    f"DROP INDEX {graph_name}_source_revision IF EXISTS"
                )
                await result.consume()
        finally:
            await driver.close()


def make_source(chunk_id, resource_id, *, description="来源描述"):
    person = GraphNode(
        name="张三",
        source_id=chunk_id,
        ontology_name="work",
        category="Person",
        description=description,
    )
    company = GraphNode(
        name="甲公司", source_id=chunk_id, ontology_name="work", category="Company"
    )
    edge = GraphEdge(
        source_id=person.id,
        target_id=company.id,
        relation_type="works_at",
        description=description,
        source_ids=[chunk_id],
        metadata={"nested": {"source": chunk_id}},
    )
    return GraphSourceProjection(
        chunk_id,
        {"resource_id": resource_id, "content_revision": "revision"},
        [person, company],
        [edge],
    )


async def test_real_gc_shared_contributions_then_orphan_cleanup(real_storage):
    storage, driver, database, graph_name = real_storage
    first, second = make_source("first", "gone"), make_source("second", "live")
    await storage.replace_sources([first, second])
    store = SimpleNamespace(
        list_destroyed_resource_ids=AsyncMock(side_effect=[["gone"], []])
    )
    gc = ResourceGarbageCollector(
        store=store,
        content_writer=SimpleNamespace(delete_resources=AsyncMock()),
        resource_acls=SimpleNamespace(delete=AsyncMock()),
        vector_writers=[],
        graph_writers=[storage],
    )
    assert await gc.collect() == GcReport(1, 1, 0)
    assert [
               node.source_id for node in await storage.get_nodes([first.nodes[0].id])
           ] == ["second"]
    assert [
               edge.source_ids for edge in await storage.get_relations([first.edges[0].id])
           ] == [["second"]]
    store.list_destroyed_resource_ids.side_effect = [["live"], []]
    assert await gc.collect() == GcReport(1, 1, 0)
    async with driver.session(database=database) as session:
        result = await session.run(
            "MATCH (n) WHERE any(label IN labels(n) WHERE label IN $labels) RETURN count(n) AS remaining",
            labels=[
                f"{graph_name}_{kind}" for kind in ("Entity", "Relation", "Source")
            ],
        )
        assert (await result.single())["remaining"] == 0


async def test_real_shared_provenance_replacement_reading_and_orphan_cleanup(
        real_storage,
):
    storage, driver, database, graph_name = real_storage
    first = make_source("a", "doc-a", description="第一份描述")
    second = make_source("b", "doc-b", description="第二份描述")
    await storage.replace_sources([first, second])
    await storage.replace_sources([first])
    assert await storage.get_nodes([first.nodes[0].id]) == [
        first.nodes[0],
        second.nodes[0],
    ]
    assert await storage.get_relations([first.edges[0].id]) == [
        first.edges[0],
        second.edges[0],
    ]
    assert await storage.get_neighbors([first.nodes[0].id], limit=1) == [
        first.edges[0],
        second.edges[0],
    ]
    assert (
            await storage.get_neighbors(
                [first.nodes[0].id], direction=GraphDirection.INCOMING
            )
            == []
    )
    assert await storage.get_neighbors(
        [first.nodes[1].id], direction=GraphDirection.INCOMING
    ) == [first.edges[0], second.edges[0]]
    selected = MetadataFilters([MetadataFilter("resource_id", "doc-b")])
    assert await storage.get_nodes([first.nodes[0].id], filters=selected) == [
        second.nodes[0]
    ]
    await storage.replace_sources([GraphSourceProjection("a", first.metadata)])
    assert await storage.get_relations([first.edges[0].id]) == second.edges
    await storage.delete_sources(selected)
    assert await storage.get_nodes([first.nodes[0].id, first.nodes[1].id]) == []
    async with driver.session(database=database) as session:
        result = await session.run(
            f"MATCH (n:{graph_name}_Entity) RETURN count(n) AS count"
        )
        assert (await result.single())["count"] == 0
        await result.consume()


async def test_real_failed_second_batch_retains_old_and_previous_commit(real_storage):
    storage, _, _, _ = real_storage
    first, second = make_source("a", "doc-a"), make_source("b", "doc-b")
    await storage.replace_sources([first, second])
    updated = replace(
        first, nodes=[replace(first.nodes[0], description="已更新"), first.nodes[1]]
    )
    # 直接调用 Writer 模拟不完整端点：MATCH 不能静默丢写，计数不符回滚第二批。
    malformed = replace(second, edges=[replace(second.edges[0], target_id="missing")])
    with pytest.raises(RuntimeError, match="all relationship facts"):
        await storage.replace_sources([updated, malformed])
    assert await storage.get_nodes([first.nodes[0].id]) == [
        updated.nodes[0],
        second.nodes[0],
    ]
    assert await storage.get_relations([first.edges[0].id]) == [
        first.edges[0],
        second.edges[0],
    ]


async def test_real_indexer_cross_document_path_and_namespace_isolation(
        real_storage, graph_ontology, chunk
):
    storage, driver, database, graph_name = real_storage
    from rag.application.publication.ingestion import GraphIndexer

    first = make_source("a", "doc-a")
    second = make_source("b", "doc-b")
    # 复用已声明的就职谓词构造反向查询种子：第二文档另一个人任职于共同公司。
    other = replace(second.nodes[0], name="李四")
    second = replace(
        second,
        nodes=[other, second.nodes[1]],
        edges=[replace(second.edges[0], source_id=other.id)],
    )
    chunks = [
        replace(
            chunk,
            chunk_id=source.chunk_id,
            resource_id=source.metadata["resource_id"],
            content_revision="revision",
            metadata={
                "entities": [asdict(node) for node in source.nodes],
                "relationships": [asdict(edge) for edge in source.edges],
            },
        )
        for source in [first, second]
    ]
    await GraphIndexer(ontology=graph_ontology, writer=storage).index(chunks)
    selected = MetadataFilters([MetadataFilter("ontology_name", graph_ontology.domain)])
    assert await storage.get_nodes([first.nodes[0].id], filters=selected) == [
        first.nodes[0]
    ]
    assert (
            await storage.get_relations([first.edges[0].id], filters=selected)
            == first.edges
    )
    assert (
            await storage.get_neighbors([first.nodes[0].id], filters=selected)
            == first.edges
    )
    denied = MetadataFilters([MetadataFilter("ontology_name", "other")])
    assert await storage.get_nodes([first.nodes[0].id], filters=denied) == []
    assert await storage.get_relations([first.edges[0].id], filters=denied) == []
    assert await storage.get_neighbors([first.nodes[0].id], filters=denied) == []
    hop = await storage.get_neighbors([first.nodes[0].id])
    assert await storage.get_neighbors(
        [hop[0].target_id], direction=GraphDirection.INCOMING
    ) == [first.edges[0], second.edges[0]]
    separate = Neo4jGraphStorage(
        driver=driver, database=database, graph_name=f"{graph_name}_separate"
    )
    assert await separate.get_nodes([first.nodes[0].id]) == []
