"""普通正文检索附带实体引用；候选补充损坏不影响已授权命中。"""

from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag.domain.models.acl import PermissionScope, ResourceAcl
from rag.application.common.snapshot import ResourceSnapshot
from rag.application.retrieval import VectorRetrieval
from rag.domain.storage.graph import GraphDirection
from rag.domain.storage.vector import VectorMatch


@pytest.fixture
def connected_retrieval(graph_env, snapshot_reader):
    env = graph_env
    vector = SimpleNamespace(
        query=AsyncMock(
            return_value=[
                VectorMatch(
                    chunk.chunk_id,
                    1.0,
                    {
                        "resource_id": chunk.resource_id,
                        "content_revision": chunk.content_revision,
                    },
                )
                for chunk in env.chunks
            ]
        )
    )
    return SimpleNamespace(
        vector=vector,
        usecase=VectorRetrieval(
            snapshot_reader=snapshot_reader,
            vector_reader=vector,
            embedding_client=env.embedding,
            graph_reader=env.graph,
        ),
    )


@pytest.mark.parametrize(
    "state",
    ["valid", "missing", "partial", "bad-source", "bad-structure"],
)
async def test_chunk_hits_entity_references_are_from_selected_authorized_chunk(
        graph_env, snapshot_reader, storage, state
):
    env = graph_env
    chunk = env.chunks[0]
    if state == "valid":
        chunk.metadata["entities"].extend(
            [
                chunk.metadata["entities"][0],
            ]
        )
    elif state == "missing":
        del chunk.metadata["entities"], chunk.metadata["relationships"]
    elif state == "partial":
        del chunk.metadata["relationships"]
    elif state == "bad-source":
        chunk.metadata["entities"][0]["source_id"] = "wrong"
    elif state == "bad-structure":
        chunk.metadata["entities"] = ["broken"]
    await storage.upsert_chunks([chunk])
    match = VectorMatch(
        chunk.chunk_id,
        1.0,
        {"resource_id": chunk.resource_id, "content_revision": chunk.content_revision},
    )
    vector = SimpleNamespace(query=AsyncMock(return_value=[match]))
    usecase = VectorRetrieval(
        snapshot_reader=snapshot_reader,
        vector_reader=vector,
        embedding_client=env.embedding,
    )
    hits = await usecase.retrieve(
        "query", snapshot=ResourceSnapshot(PermissionScope("reader"))
    )
    assert len(hits) == 1 and hits[0].text == chunk.raw_text
    if state == "valid":
        assert [node.id for node in hits[0].seed_entities] == [
            node.id for node in env.facts[0][0]
        ]
        assert set(asdict(hits[0].seed_entities[0])) == {"id", "relations"}
        assert all(seed.relations is None for seed in hits[0].seed_entities)
    else:
        assert hits[0].seed_entities == []
    env.graph.get_nodes.assert_not_awaited()
    env.graph.get_neighbors.assert_not_awaited()


async def test_hit_metadata_filters_graph_records_without_mutating_source(
        graph_env, connected_retrieval, storage
):
    env = graph_env
    chunk = env.chunks[0]
    chunk.metadata["business"] = {"employee": "张三", "tags": ["任职"]}
    original = deepcopy(chunk.metadata)
    await storage.upsert_chunks([chunk])
    snapshot = env.snapshot()
    hits = await connected_retrieval.usecase.retrieve(
        "query", snapshot=snapshot, mode="dense", top_k=1
    )
    assert set(hits[0].metadata) == {"contextual_context", "other", "business"}
    assert hits[0].metadata["business"] == original["business"]
    assert hits[0].metadata["other"] == original["other"]
    assert hits[0].metadata["contextual_context"] == original["contextual_context"]
    assert hits[0].seed_entities
    hits[0].metadata["response_only"] = True
    again = await connected_retrieval.usecase.retrieve(
        "query", snapshot=snapshot, mode="dense", top_k=1
    )
    assert "response_only" not in again[0].metadata


async def test_graph_seed_parser_program_errors_propagate_from_retrieval(
        graph_env, connected_retrieval, storage, monkeypatch
):
    env = graph_env
    chunk = env.chunks[0]
    chunk.metadata.update(entities=[], relationships=[])
    await storage.upsert_chunks([chunk])

    def broken_parser(value):
        raise TypeError("parser program error")

    monkeypatch.setattr("rag.application.retrieval.parse_graph_facts", broken_parser)
    with pytest.raises(TypeError, match="parser program error"):
        await connected_retrieval.usecase.retrieve(
            "query", snapshot=env.snapshot(), mode="dense", top_k=1
        )


async def test_connections_span_other_chunks_and_only_final_hits_are_queried(
        graph_env, connected_retrieval
):
    env = graph_env
    hits = await connected_retrieval.usecase.retrieve(
        "query", snapshot=env.snapshot(), mode="dense", top_k=1
    )
    seeds = {seed.id: seed for seed in hits[0].seed_entities}
    person, company = env.facts[0][0]
    assert [
               (option.relation_type, option.direction)
               for option in seeds[company.id].relations
           ] == [("supplies", GraphDirection.OUTGOING), ("works_at", GraphDirection.INCOMING)]
    assert seeds[person.id].relations[0].direction is GraphDirection.OUTGOING
    env.graph.get_neighbors.assert_awaited_once()
    request = env.graph.get_neighbors.call_args
    assert request.args[0] == [person.id, company.id]
    assert request.kwargs["limit"] is None and request.kwargs["filters"] is None
    assert hits[0].metadata == {
        key: value
        for key, value in env.chunks[0].metadata.items()
        if key not in {"entities", "relationships"}
    }


@pytest.mark.parametrize("hidden", ["denied", "destroyed", "stale", "missing-acl"])
async def test_hidden_sources_do_not_prove_connections(
        graph_env,
        connected_retrieval,
        storage,
        publication_store,
        publish_read_document,
        make_read_document,
        read_acls,
        hidden,
):
    env = graph_env
    # a 仍提供人员实体，只有 c 能为其关系背书；写入时的本体提示保持存在。
    env.chunks[0].metadata["relationships"] = []
    await storage.upsert_chunks([env.chunks[0]])
    if hidden == "denied":
        read_acls["c"] = ResourceAcl("c", 1, "other")
    elif hidden == "destroyed":
        await publication_store.destroy_resources(["c"])
    elif hidden == "stale":
        await publish_read_document(make_read_document("c", version=2))
    else:
        read_acls.pop("c")
    hits = await connected_retrieval.usecase.retrieve(
        "query", snapshot=env.snapshot(), mode="dense", top_k=1
    )
    assert hits[0].seed_entities[0].relations == []
    assert env.chunks[0].metadata["entities"][0]["relations"]
    assert "entities" not in hits[0].metadata


@pytest.mark.parametrize("self_loop", [False, True])
async def test_connections_merge_opposite_directions_and_self_loops(
        graph_env, connected_retrieval, storage, self_loop
):
    env = graph_env
    chunk = env.chunks[1]
    edge = env.facts[1][1][0]
    extra = replace(
        edge,
        source_id=edge.target_id if not self_loop else edge.source_id,
        target_id=edge.source_id,
    )
    chunk.metadata["relationships"].append(asdict(extra))
    await storage.upsert_chunks([chunk])
    env.graph.edges.append(extra)
    hits = await connected_retrieval.usecase.retrieve(
        "query", snapshot=env.snapshot(), mode="dense", top_k=1
    )
    company = hits[0].seed_entities[1]
    assert (
            next(
                option for option in company.relations if option.relation_type == "supplies"
            ).direction
            is GraphDirection.BOTH
    )


async def test_connections_beyond_one_hundred_are_not_lost(
        graph_env, connected_retrieval, storage
):
    env = graph_env
    chunk = env.chunks[1]
    original = env.facts[1][1][0]
    edges = [
        replace(original, relation_type=f"connection_{index:03}")
        for index in range(105)
    ]
    chunk.metadata["relationships"] = [asdict(edge) for edge in edges]
    await storage.upsert_chunks([chunk])
    env.graph.edges.extend(edges)
    hits = await connected_retrieval.usecase.retrieve(
        "query", snapshot=env.snapshot(), mode="dense", top_k=1
    )
    types = {option.relation_type for option in hits[0].seed_entities[1].relations}
    assert "connection_104" in types and len(types) == 106


@pytest.mark.parametrize("damage", ["network", "facts"])
async def test_enabled_connection_failures_propagate(
        graph_env, connected_retrieval, storage, damage
):
    env = graph_env
    if damage == "network":
        env.graph.get_neighbors.side_effect = OSError("graph unavailable")
        expected = OSError
    else:
        env.chunks[1].metadata.pop("relationships")
        await storage.upsert_chunks([env.chunks[1]])
        expected = ValueError
    with pytest.raises(expected):
        await connected_retrieval.usecase.retrieve(
            "query", snapshot=env.snapshot(), mode="dense", top_k=1
        )
