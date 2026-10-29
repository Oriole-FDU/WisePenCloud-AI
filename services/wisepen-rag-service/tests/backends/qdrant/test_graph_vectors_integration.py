"""显式隔离 Qdrant 上验证 dense 图来源替换与三路种子召回。"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag.ingestion.indexers import GraphIndexer
from rag.retrievers import GraphRetrievalMode, GraphRetriever
from rag.domain.storage.graph import GraphVectorKind, graph_fact_sha256
from rag.domain.storage.vector import VectorQuery

pytestmark = pytest.mark.qdrant_integration


@pytest.fixture
def embedding():
    async def embed(*, texts):
        return [[1.0, 0.0] for _ in texts]

    return SimpleNamespace(dimensions=2, embed=AsyncMock(side_effect=embed))


async def test_real_source_replacement_fact_reduction_empty_and_other_source_preservation(
        real_graph_vectors, graph_ontology, chunk, make_graph_facts, embedding
):
    entities, relationships, _ = real_graph_vectors
    make_graph_facts(chunk)
    second = replace(chunk, chunk_id="second", metadata={})
    second_nodes, _ = make_graph_facts(second, description="第二份描述")
    second.metadata["graph_version"] = "second-graph-version"
    second_nodes_by_id = {node.id: node for node in second_nodes}
    expected_fact_hashes = {
        node_id: graph_fact_sha256(node, second_nodes_by_id)
        for node_id, node in second_nodes_by_id.items()
    }
    indexer = GraphIndexer(
        ontology=graph_ontology,
        embedding_client=embedding,
        entity_writer=entities,
        relationship_writer=relationships,
    )
    await indexer.index([chunk, second])

    async def counts():
        return [
            len(await storage.query(VectorQuery(embedding=[1.0, 0.0], top_k=100)))
            for storage in (entities, relationships)
        ]

    assert await counts() == [4, 2]
    await indexer.index([chunk])
    assert await counts() == [4, 2]
    chunk.metadata["entities"] = chunk.metadata["entities"][:1]
    chunk.metadata["relationships"] = []
    await indexer.index([chunk])
    assert await counts() == [3, 1]
    chunk.metadata.update(entities=[], relationships=[])
    await indexer.index([chunk])
    assert await counts() == [2, 1]
    matches = await entities.query(VectorQuery(embedding=[1.0, 0.0], top_k=100))
    assert all(match.metadata["chunk_id"] == "second" for match in matches)
    assert all(
        match.metadata["fact_sha256"]
        == expected_fact_hashes[match.metadata["fact_id"]]
        for match in matches
    )
    assert all(
        match.metadata["graph_version"] == "second-graph-version"
        for match in matches
    )


@pytest.mark.parametrize("mode", list(GraphRetrievalMode))
async def test_real_graph_seed_modes_with_dense_only_collections(
        real_graph_vectors, graph_ontology, chunk, make_graph_facts, embedding, mode
):
    entities, relationships, _ = real_graph_vectors
    make_graph_facts(chunk)
    await GraphIndexer(
        ontology=graph_ontology,
        embedding_client=embedding,
        entity_writer=entities,
        relationship_writer=relationships,
    ).index([chunk])
    embedding.embed.reset_mock()
    retriever = GraphRetriever.from_storage(
        entity_reader=entities,
        relationship_reader=relationships,
        embedding_client=embedding,
        mode=mode,
        include_embedding=True,
    )
    results = await retriever.retrieve("任职人员")
    expected = (
        {GraphVectorKind.ENTITY}
        if mode is GraphRetrievalMode.LOCAL
        else {GraphVectorKind.RELATIONSHIP}
        if mode is GraphRetrievalMode.GLOBAL
        else set(GraphVectorKind)
    )
    assert {result.payload.kind for result in results} == expected
    assert all(
        result.payload.vector.embedding == [1.0, 0.0]
        and result.payload.vector.metadata["chunk_id"] == chunk.chunk_id
        for result in results
    )
    embedding.embed.assert_awaited_once_with(texts=["任职人员"])
