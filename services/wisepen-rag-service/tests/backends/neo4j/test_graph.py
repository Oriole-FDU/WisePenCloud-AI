"""驱动替身下验证来源投影、参数、读回和事务失败，不声称验证真实 Cypher。"""

import asyncio
import copy
from dataclasses import asdict, replace

import pytest

from rag.backends.neo4j import Neo4jGraphStorage
from rag.core.schema import GraphEdge, GraphNode
from rag.core.schema.filters import (
    MetadataFilter,
    MetadataFilters,
)
from rag.ingestion.indexers import ChunkFactsIndexer, GraphIndexer
from rag.storage.graph import GraphDirection, GraphSourceProjection


def projection(
        chunk_id,
        resource="resource",
        *,
        name="Alice",
        target="Acme",
        description="source description",
        revision="revision",
):
    source = GraphNode(
        name=name,
        source_id=chunk_id,
        ontology_name="work",
        category="Person",
        description=description,
    )
    company = GraphNode(
        name=target, source_id=chunk_id, ontology_name="work", category="Company"
    )
    edge = GraphEdge(
        source_id=source.id,
        target_id=company.id,
        relation_type="works_at",
        source_ids=[chunk_id],
        description=description,
        keywords=["工作"],
        metadata={"nested": {"source": chunk_id}},
    )
    return GraphSourceProjection(
        chunk_id,
        {"resource_id": resource, "content_revision": revision},
        [source, company],
        [edge],
    )


def resource_filter(resource):
    return MetadataFilters([MetadataFilter("resource_id", resource)])


async def test_unlimited_neighbors_omit_cypher_limit_and_keep_all_sources(driver):
    storage = Neo4jGraphStorage(driver=driver)
    first = projection("first")
    first = replace(
        first,
        edges=[
            replace(first.edges[0], relation_type=f"r_{index:03}")
            for index in range(105)
        ],
    )
    second = projection("second")
    second = replace(
        second, edges=[replace(edge, source_ids=["second"]) for edge in first.edges]
    )
    await storage.replace_sources([first, second])
    assert len(await storage.get_neighbors([first.nodes[0].id])) == 200
    edges = await storage.get_neighbors([first.nodes[0].id], limit=None)
    assert len(edges) == 210 and len({edge.id for edge in edges}) == 105
    query = next(
        query
        for operation, query, _ in reversed(driver.calls)
        if operation == "get_neighbors"
    )
    assert "LIMIT $limit" not in query


async def test_shared_entities_relationships_keep_all_source_facts_and_request_order(
        driver,
):
    storage = Neo4jGraphStorage(driver=driver)
    first = projection("z", "first", description="first description")
    second = projection("a", "second", description="second description")
    before = copy.deepcopy([first, second])
    assert await storage.replace_sources([first, second]) is None
    assert [first, second] == before
    assert len(driver.state["nodes"]) == 2 and len(driver.state["edges"]) == 1
    nodes = await storage.get_nodes(
        [first.nodes[1].id, first.nodes[0].id, first.nodes[1].id, "missing"]
    )
    assert [node.id for node in nodes] == [first.nodes[1].id] * 2 + [
        first.nodes[0].id
    ] * 2
    assert [node.source_id for node in nodes] == ["a", "z", "a", "z"]
    assert [node.description for node in nodes[2:]] == [
        "second description",
        "first description",
    ]
    relations = await storage.get_relations([first.edges[0].id])
    assert [edge.source_ids for edge in relations] == [["a"], ["z"]]
    assert [edge.description for edge in relations] == [
        "second description",
        "first description",
    ]
    assert relations[0].metadata == {"nested": {"source": "a"}}
    assert await storage.get_nodes(
        [first.nodes[0].id], filters=resource_filter("first")
    ) == [first.nodes[0]]
    assert (
            await storage.get_relations(
                [first.edges[0].id], filters=resource_filter("second")
            )
            == second.edges
    )
    assert all(result.consumed for result in driver.results)


async def test_same_source_replacement_removes_old_facts_and_preserves_other_sources(
        driver,
):
    storage = Neo4jGraphStorage(driver=driver)
    first = projection("first", description="old")
    second = projection("second", description="other")
    await storage.replace_sources([first, second])
    changed = projection("first", target="NewCompany", description="new")
    await storage.replace_sources([changed])
    assert await storage.get_relations([first.edges[0].id]) == second.edges
    assert await storage.get_relations([changed.edges[0].id]) == changed.edges
    assert {
               node.source_id for node in await storage.get_nodes([first.nodes[0].id])
           } == {"first", "second"}
    assert await storage.get_nodes([changed.nodes[1].id]) == [changed.nodes[1]]
    await storage.replace_sources([GraphSourceProjection("first", changed.metadata)])
    assert await storage.get_relations([changed.edges[0].id]) == []
    assert changed.nodes[1].id not in driver.state["nodes"]
    assert changed.edges[0].id not in driver.state["edges"]
    assert await storage.get_relations([first.edges[0].id]) == second.edges
    # 重放空成功仍幂等，不删除另一个来源。
    await storage.replace_sources([GraphSourceProjection("first", changed.metadata)])
    assert set(driver.state["sources"]) == {"second"}


async def test_delete_filtered_sources_preserves_shared_identity_then_cleans_orphans(
        driver,
):
    storage = Neo4jGraphStorage(driver=driver)
    first, second = projection("first", "a"), projection("second", "b")
    await storage.replace_sources([first, second])
    await storage.delete_sources(resource_filter("a"))
    assert await storage.get_relations([first.edges[0].id]) == second.edges
    await storage.delete_sources(resource_filter("b"))
    assert driver.state == {"sources": {}, "nodes": set(), "edges": {}}


async def test_direction_predicates_and_full_sources_under_logical_limit(driver):
    storage = Neo4jGraphStorage(driver=driver)
    first, second = projection("a"), projection("b")
    reverse = replace(
        first,
        chunk_id="reverse",
        nodes=[replace(node, source_id="reverse") for node in first.nodes],
        edges=[
            replace(
                first.edges[0],
                source_id=first.edges[0].target_id,
                target_id=first.edges[0].source_id,
                source_ids=["reverse"],
            )
        ],
    )
    other = replace(
        first,
        chunk_id="other",
        nodes=[replace(node, source_id="other") for node in first.nodes],
        edges=[replace(first.edges[0], relation_type="manages", source_ids=["other"])],
    )
    await storage.replace_sources([first, second, reverse, other])
    source_id = first.nodes[0].id
    outgoing = await storage.get_neighbors(
        [source_id], relation_types=["works_at"], limit=1
    )
    assert [edge.source_ids for edge in outgoing] == [["a"], ["b"]]
    incoming = await storage.get_neighbors(
        [source_id], direction=GraphDirection.INCOMING
    )
    assert incoming == reverse.edges
    both = await storage.get_neighbors([source_id], direction=GraphDirection.BOTH)
    assert {edge.id for edge in both} == {
        first.edges[0].id,
        reverse.edges[0].id,
        other.edges[0].id,
    }


async def test_cross_document_two_hops_and_revision_filters(driver):
    storage = Neo4jGraphStorage(driver=driver)
    first = projection("a", "doc-a")
    second = projection("b", "doc-b")
    project = GraphNode(
        name="Project", source_id="b", ontology_name="work", category="Project"
    )
    second = replace(
        second,
        nodes=[second.nodes[1], project],
        edges=[
            GraphEdge(
                source_id=second.nodes[1].id,
                target_id=project.id,
                relation_type="participates",
                source_ids=["b"],
            )
        ],
    )
    old = projection("old", "doc-a", revision="old")
    await storage.replace_sources([first, second, old])
    selected = MetadataFilters([MetadataFilter("content_revision", "revision")])
    hop = await storage.get_neighbors([first.nodes[0].id], filters=selected)
    assert hop == first.edges
    assert (
            await storage.get_neighbors([hop[0].target_id], filters=selected)
            == second.edges
    )


@pytest.mark.parametrize(
    "operation", ["upsert_sources", "upsert_nodes", "upsert_edges", "cleanup_relations"]
)
async def test_failed_batch_rolls_back_and_previous_batch_remains(driver, operation):
    storage = Neo4jGraphStorage(driver=driver, batch_size=1)
    first, second = projection("first"), projection("second", description="old second")
    await storage.replace_sources([first, second])
    driver.fail_transaction = driver.transactions + 2
    driver.fail_operation = operation
    changed_first = projection("first", description="new first")
    changed_second = projection("second", description="new second")
    with pytest.raises(OSError):
        await storage.replace_sources([changed_first, changed_second])
    assert await storage.get_nodes([first.nodes[0].id]) == [
        changed_first.nodes[0],
        second.nodes[0],
    ]
    assert driver.rollbacks == 1
    driver.fail_operation = None
    await storage.replace_sources([changed_first, changed_second])
    assert await storage.get_nodes([first.nodes[0].id]) == [
        changed_first.nodes[0],
        changed_second.nodes[0],
    ]


@pytest.mark.parametrize("failure", ["count-node", "count-edge", "consume", "commit"])
async def test_incomplete_writes_and_confirmation_errors_do_not_commit(driver, failure):
    storage = Neo4jGraphStorage(driver=driver)
    before = projection("source", description="old")
    await storage.replace_sources([before])
    if failure == "count-node":
        driver.bad_count = "upsert_nodes"
    elif failure == "count-edge":
        driver.bad_count = "upsert_edges"
    elif failure == "consume":
        driver.consume_failure = "upsert_edges"
    else:
        driver.commit_failure = True
    with pytest.raises((RuntimeError, OSError)):
        await storage.replace_sources([projection("source", description="new")])
    assert driver.commits == 1 and driver.rollbacks == 1
    driver.consume_failure = None
    assert await storage.get_nodes([before.nodes[0].id]) == [before.nodes[0]]


async def test_cancelled_transaction_retains_old_contribution(driver):
    storage = Neo4jGraphStorage(driver=driver)
    source = projection("source")
    await storage.replace_sources([source])
    driver.block_operation = "upsert_edges"
    task = asyncio.create_task(
        storage.replace_sources([projection("source", description="new")])
    )
    await driver.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert driver.rollbacks == 1
    assert await storage.get_relations([source.edges[0].id]) == source.edges


async def test_empty_calls_validation_serialization_and_explicit_schema(driver):
    storage = Neo4jGraphStorage(driver=driver, graph_name="test_graph")
    assert await storage.replace_sources([]) is None
    assert await storage.get_nodes([]) == []
    assert await storage.get_relations([]) == []
    assert await storage.get_neighbors([]) == []
    assert await storage.get_neighbors(["a"], relation_types=[]) == []
    assert driver.calls == []
    for invoke in (
            storage.delete_sources(MetadataFilters()),
            storage.get_nodes(["a"], filters=MetadataFilters()),
            storage.get_neighbors(["a"], limit=0),
            storage.get_neighbors(["a"], direction="unsupported"),
    ):
        with pytest.raises(ValueError):
            await invoke
    value = projection("source")
    value.edges[0].metadata["unsupported"] = object()
    with pytest.raises(TypeError):
        await storage.replace_sources([projection("valid"), value])
    assert driver.calls == []
    await storage.ensure_schema()
    assert len(driver.calls) == 4
    assert all("test_graph" in query for _, query, _ in driver.calls)
    assert all(result.consumed for result in driver.results)


async def test_identity_corruption_is_rejected_on_read(driver):
    storage = Neo4jGraphStorage(driver=driver)
    value = projection("source")
    await storage.replace_sources([value])
    payload = driver.state["sources"]["source"]["nodes"][value.nodes[0].id]
    driver.state["sources"]["source"]["nodes"][value.nodes[0].id] = payload.replace(
        '"source_id": "source"', '"source_id": "other"'
    )
    with pytest.raises(RuntimeError, match="identity"):
        await storage.get_nodes([value.nodes[0].id])


@pytest.mark.parametrize(
    "settings",
    [
        {"graph_name": "bad`name"},
        {"graph_name": ""},
        {"database": " "},
        {"batch_size": True},
        {"batch_size": 0},
    ],
)
def test_invalid_backend_settings(driver, settings):
    with pytest.raises(ValueError):
        Neo4jGraphStorage(driver=driver, **settings)


async def test_persisted_mongo_facts_to_indexer_to_topology(
        driver, storage, graph_ontology, chunk
):
    sources = [projection(chunk.chunk_id), projection("second")]
    chunks = [
        replace(
            chunk,
            chunk_id=source.chunk_id,
            chunk_index=index,
            metadata={
                **chunk.metadata,
                "entities": [asdict(node) for node in source.nodes],
                "relationships": [asdict(edge) for edge in source.edges],
            },
        )
        for index, source in enumerate(sources)
    ]
    texts = [value.retrieval_text for value in chunks]
    await ChunkFactsIndexer(storage).index(chunks)
    stored = await storage.get_revision_chunks(
        resource_id=chunk.resource_id, content_revision=chunk.content_revision
    )
    topology = Neo4jGraphStorage(driver=driver)
    await GraphIndexer(ontology=graph_ontology, writer=topology).index(stored)
    assert [value.retrieval_text for value in stored] == texts
    assert await topology.get_nodes([sources[0].nodes[0].id]) == [
        source.nodes[0] for source in sources
    ]
    assert await topology.get_relations([sources[0].edges[0].id]) == [
        source.edges[0] for source in sources
    ]
    selected = MetadataFilters([MetadataFilter("ontology_name", graph_ontology.domain)])
    assert await topology.get_nodes([sources[0].nodes[0].id], filters=selected) == [
        source.nodes[0] for source in sources
    ]
    assert await topology.get_relations([sources[0].edges[0].id], filters=selected) == [
        source.edges[0] for source in sources
    ]
    assert await topology.get_neighbors([sources[0].nodes[0].id], filters=selected) == [
        source.edges[0] for source in sources
    ]
    denied = MetadataFilters([MetadataFilter("ontology_name", "other")])
    assert await topology.get_nodes([sources[0].nodes[0].id], filters=denied) == []
    assert await topology.get_relations([sources[0].edges[0].id], filters=denied) == []
    assert await topology.get_neighbors([sources[0].nodes[0].id], filters=denied) == []

    foreign = replace(graph_ontology, domain="other")
    other_chunk = replace(chunk, chunk_id="foreign", metadata={})
    other_nodes = [
        foreign.create_node(
            name=node.name, category=node.category, source_id=other_chunk.chunk_id
        )
        for node in sources[0].nodes
    ]
    other_edge = foreign.create_edge(
        nodes={node.id: node for node in other_nodes},
        source_id=other_nodes[0].id,
        target_id=other_nodes[1].id,
        relation_type="works_at",
        chunk_id=other_chunk.chunk_id,
    )
    other_chunk.metadata.update(
        entities=[asdict(node) for node in other_nodes],
        relationships=[asdict(other_edge)],
    )
    await GraphIndexer(ontology=foreign, writer=topology).index([other_chunk])
    ids = [other_nodes[0].id, sources[0].nodes[0].id]
    assert await topology.get_nodes(ids, filters=selected) == [
        source.nodes[0] for source in sources
    ]
    assert await topology.get_relations(
        [other_edge.id, sources[0].edges[0].id], filters=selected
    ) == [source.edges[0] for source in sources]
    # 本体条件先于逻辑关系预算执行，其他本体不能占据名额。
    assert await topology.get_neighbors(ids, limit=1) == [other_edge]
    assert await topology.get_neighbors(ids, filters=selected, limit=1) == [
        source.edges[0] for source in sources
    ]
