"""来源向量替换的校验、批次、空贡献和失败边界。"""

import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag.ingestion.indexers import GraphIndexer


@pytest.fixture
def indexer(graph_ontology, graph_vector_writers):
    entities, relationships = graph_vector_writers

    async def embed(*, texts):
        return [[1.0, 0.0] for _ in texts]

    embedding = SimpleNamespace(dimensions=2, embed=AsyncMock(side_effect=embed))
    return GraphIndexer(
        ontology=graph_ontology,
        embedding_client=embedding,
        entity_writer=entities,
        relationship_writer=relationships,
    ), embedding


async def test_per_source_semantics_readonly_and_replay(
    indexer, chunk, make_graph_facts, graph_vector_writers
):
    usecase, embedding = indexer
    make_graph_facts(chunk, description="首个来源")
    second = replace(
        chunk,
        chunk_id="second",
        resource_id="other",
        content_revision="other-revision",
        metadata={"contextual_context": "不索引"},
    )
    make_graph_facts(second, description="第二来源")
    before = deepcopy([chunk, second])
    await usecase.index([chunk, second])
    entities, relationships = graph_vector_writers
    assert [chunk, second] == before
    assert len(entities.records) == 4 and len(relationships.records) == 2
    assert all("工作关系" in record.text for record in entities.records.values())
    assert {record.metadata["chunk_id"] for record in entities.records.values()} == {
        chunk.chunk_id,
        "second",
    }
    assert all(record.embedding == [1.0, 0.0] for record in entities.records.values())
    assert all("不索引" not in record.text for record in entities.records.values())
    assert {
        record.text.split("description: ")[-1] for record in entities.records.values()
    } == {"首个来源", "第二来源"}
    original = deepcopy(entities.records)
    await usecase.index([chunk])
    assert entities.records == original
    assert embedding.embed.await_count == 2


async def test_fact_reduction_and_empty_clear_only_given_source(
    indexer, chunk, make_graph_facts, graph_vector_writers
):
    usecase, embedding = indexer
    make_graph_facts(chunk)
    second = replace(chunk, chunk_id="second", metadata={})
    make_graph_facts(second)
    await usecase.index([chunk, second])
    chunk.metadata["entities"] = chunk.metadata["entities"][:1]
    chunk.metadata["relationships"] = []
    await usecase.index([chunk])
    entities, relationships = graph_vector_writers
    assert len(entities.records) == 3 and len(relationships.records) == 1
    chunk.metadata.update(entities=[], relationships=[])
    embedding.embed.reset_mock()
    await usecase.index([chunk])
    assert len(entities.records) == 2 and len(relationships.records) == 1
    embedding.embed.assert_not_awaited()
    assert all(
        record.metadata["chunk_id"] == "second" for record in entities.records.values()
    )


@pytest.mark.parametrize(
    "damage", ["missing", "shape", "source", "edge-source", "duplicate-source"]
)
async def test_all_input_validated_before_io(
    indexer, chunk, make_graph_facts, graph_vector_writers, damage
):
    usecase, embedding = indexer
    make_graph_facts(chunk)
    broken = replace(chunk, chunk_id="broken", metadata={})
    make_graph_facts(broken)
    if damage == "missing":
        broken.metadata.pop("entities")
    elif damage == "shape":
        broken.metadata["entities"][0]["name"] = []
    elif damage == "source":
        broken.metadata["entities"][0]["source_id"] = "wrong"
    elif damage == "edge-source":
        broken.metadata["relationships"][0]["source_ids"] = ["wrong"]
    else:
        broken.chunk_id = chunk.chunk_id
    with pytest.raises((ValueError, TypeError)):
        await usecase.index([chunk, broken])
    embedding.embed.assert_not_awaited()
    for writer in graph_vector_writers:
        writer.delete.assert_not_awaited()
        writer.upsert.assert_not_awaited()


async def test_ontology_invalid_and_duplicate_facts(
    indexer, chunk, make_graph_facts, graph_vector_writers
):
    nodes, edges = make_graph_facts(chunk)
    chunk.metadata["entities"] += [
        asdict(replace(nodes[0], category="unknown")),
        asdict(replace(nodes[0], description="不覆盖")),
    ]
    chunk.metadata["relationships"] += [
        asdict(replace(edges[0], relation_type="unknown")),
        asdict(replace(edges[0], source_id=nodes[1].id, target_id=nodes[0].id)),
        asdict(replace(edges[0], description="不覆盖")),
    ]
    await indexer[0].index([chunk])
    assert [len(writer.records) for writer in graph_vector_writers] == [2, 1]
    assert all(
        "不覆盖" not in record.text
        for writer in graph_vector_writers
        for record in writer.records.values()
    )


@pytest.mark.parametrize(
    "vectors", [[], [[1.0]], [[float("nan"), 0.0]], [[True, 0.0]], [[]]]
)
async def test_bad_embedding_does_not_delete_current_batch(
    indexer, chunk, make_graph_facts, graph_vector_writers, vectors
):
    make_graph_facts(chunk)
    usecase, embedding = indexer

    async def embed(*, texts):
        if not vectors:
            raise ValueError("embedding response count does not match texts")
        return [vectors[0] for _ in texts]

    embedding.embed.side_effect = embed
    with pytest.raises(RuntimeError, match="embedding"):
        await usecase.index([chunk])
    for writer in graph_vector_writers:
        writer.delete.assert_not_awaited()


async def test_later_embedding_failure_preserves_prior_batch(
    indexer, chunk, make_graph_facts, graph_vector_writers
):
    usecase, embedding = indexer
    usecase._source_batch_size = 1
    usecase._embedding_batch_size = 2
    make_graph_facts(chunk)
    second = replace(chunk, chunk_id="second", metadata={})
    make_graph_facts(second)
    embedding.embed.side_effect = [
        [[1.0, 0.0]] * 2,
        [[1.0, 0.0]],
        OSError("embedding failed"),
    ]
    with pytest.raises(OSError):
        await usecase.index([chunk, second])
    assert [len(writer.records) for writer in graph_vector_writers] == [2, 1]
    assert all(writer.delete.await_count == 1 for writer in graph_vector_writers)
    assert [len(call.kwargs["texts"]) for call in embedding.embed.await_args_list] == [
        2,
        1,
        2,
    ]


@pytest.mark.parametrize("failure", ["delete", "upsert", "cancel"])
async def test_partial_write_then_replay(
    indexer, chunk, make_graph_facts, graph_vector_writers, failure
):
    usecase, _ = indexer
    make_graph_facts(chunk)
    entities, relationships = graph_vector_writers
    method = relationships.delete if failure == "delete" else relationships.upsert
    method.side_effect = (
        asyncio.CancelledError() if failure == "cancel" else OSError("write failed")
    )
    with pytest.raises((OSError, asyncio.CancelledError)):
        await usecase.index([chunk])
    assert len(entities.records) == (0 if failure == "delete" else 2)
    method.side_effect = (
        relationships._delete if failure == "delete" else relationships._upsert
    )
    await usecase.index([chunk])
    assert len(entities.records) == 2 and len(relationships.records) == 1


async def test_empty_input_has_no_io(indexer, graph_vector_writers):
    await indexer[0].index([])
    indexer[1].embed.assert_not_awaited()
    for writer in graph_vector_writers:
        writer.delete.assert_not_awaited()


@pytest.mark.parametrize("failure", [None, "topology", "embedding"])
async def test_combined_graph_indexer_orders_topology_before_vectors(
    indexer, chunk, make_graph_facts, graph_vector_writers, failure
):
    usecase, embedding = indexer
    make_graph_facts(chunk)
    order = []

    async def write(projections):
        order.append("topology")
        assert len(projections[0].nodes) == 2 and len(projections[0].edges) == 1
        embedding.embed.assert_not_awaited()
        if failure == "topology":
            raise OSError("topology failed")

    async def embed(*, texts):
        order.append("embedding")
        if failure == "embedding":
            raise OSError("embedding failed")
        return [[1.0, 0.0] for _ in texts]

    usecase._writer = SimpleNamespace(replace_sources=AsyncMock(side_effect=write))
    embedding.embed.side_effect = embed
    if failure:
        with pytest.raises(OSError):
            await usecase.index([chunk])
        assert all(writer.delete.await_count == 0 for writer in graph_vector_writers)
    else:
        await usecase.index([chunk])
        assert [len(writer.records) for writer in graph_vector_writers] == [2, 1]
    assert order == (
        ["topology"] if failure == "topology" else ["topology", "embedding"]
    )


def test_projection_dependencies_are_complete(graph_ontology, graph_vector_writers):
    with pytest.raises(ValueError, match="projection writer"):
        GraphIndexer(ontology=graph_ontology)
    with pytest.raises(ValueError, match="both vector writers"):
        GraphIndexer(
            ontology=graph_ontology,
            writer=SimpleNamespace(),
            entity_writer=graph_vector_writers[0],
        )
