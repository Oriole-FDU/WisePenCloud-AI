"""来源向量替换的校验、批次、空贡献和失败边界。"""

import asyncio
from copy import deepcopy
from dataclasses import replace
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


async def test_bad_embedding_does_not_delete_current_batch(
        indexer, chunk, make_graph_facts, graph_vector_writers
):
    make_graph_facts(chunk)
    usecase, embedding = indexer
    original = ValueError("embedding response count, dimensions or values are invalid")
    embedding.embed.side_effect = original

    with pytest.raises(RuntimeError, match="embedding") as caught:
        await usecase.index([chunk])
    assert caught.value.__cause__ is original
    for writer in graph_vector_writers:
        writer.delete.assert_not_awaited()


async def test_later_embedding_failure_preserves_prior_batch(
        indexer, graph_ontology, chunk, make_graph_facts, graph_vector_writers
):
    _, embedding = indexer
    entities, relationships = graph_vector_writers
    usecase = GraphIndexer(
        ontology=graph_ontology,
        embedding_client=embedding,
        entity_writer=entities,
        relationship_writer=relationships,
        source_batch_size=1,
        embedding_batch_size=2,
    )
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
async def test_partial_write_failure_can_be_replayed(
        indexer, chunk, make_graph_facts, graph_vector_writers, failure
):
    usecase, _ = indexer
    make_graph_facts(chunk)
    entities, relationships = graph_vector_writers
    method = relationships.delete if failure == "delete" else relationships.upsert
    original_write = method.side_effect
    method.side_effect = (
        asyncio.CancelledError() if failure == "cancel" else OSError("write failed")
    )
    with pytest.raises((OSError, asyncio.CancelledError)):
        await usecase.index([chunk])
    assert len(entities.records) == (0 if failure == "delete" else 2)
    method.side_effect = original_write
    await usecase.index([chunk])
    assert len(entities.records) == 2 and len(relationships.records) == 1


async def test_empty_input_has_no_io(indexer, graph_vector_writers):
    await indexer[0].index([])
    indexer[1].embed.assert_not_awaited()
    for writer in graph_vector_writers:
        writer.delete.assert_not_awaited()


@pytest.mark.parametrize("failure", [None, "topology", "embedding"])
async def test_graph_indexer_preserves_projection_failure_boundaries(
        indexer, graph_ontology, chunk, make_graph_facts, graph_vector_writers, failure
):
    _, embedding = indexer
    entities, relationships = graph_vector_writers
    make_graph_facts(chunk)

    async def write(projections):
        assert len(projections[0].nodes) == 2 and len(projections[0].edges) == 1
        if failure == "topology":
            raise OSError("topology failed")

    async def embed(*, texts):
        if failure == "embedding":
            raise OSError("embedding failed")
        return [[1.0, 0.0] for _ in texts]

    topology_writer = SimpleNamespace(replace_sources=AsyncMock(side_effect=write))
    embedding.embed.side_effect = embed
    usecase = GraphIndexer(
        ontology=graph_ontology,
        writer=topology_writer,
        embedding_client=embedding,
        entity_writer=entities,
        relationship_writer=relationships,
    )
    if failure:
        with pytest.raises(OSError):
            await usecase.index([chunk])
        # 拓扑不可用时，不能留下无法追溯来源的向量投影。
        if failure == "topology":
            embedding.embed.assert_not_awaited()
        assert entities.records == {}
        assert relationships.records == {}
    else:
        await usecase.index([chunk])
        assert [len(writer.records) for writer in graph_vector_writers] == [2, 1]


def test_projection_dependencies_are_complete(graph_ontology, graph_vector_writers):
    with pytest.raises(ValueError, match="projection writer"):
        GraphIndexer(ontology=graph_ontology)
    with pytest.raises(ValueError, match="both vector writers"):
        GraphIndexer(
            ontology=graph_ontology,
            writer=SimpleNamespace(),
            entity_writer=graph_vector_writers[0],
        )
