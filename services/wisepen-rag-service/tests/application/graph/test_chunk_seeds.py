"""普通正文检索附带实体引用；候选补充损坏不影响已授权命中。"""

from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag.application.acl.models import PermissionScope
from rag.application.retrieval import ChunkHit, VectorRetrieval
from rag.application.snapshot import ResourceSnapshot
from rag.storage.vector import VectorMatch


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
        assert [
            (option.relation_type, option.direction)
            for option in hits[0].seed_entities[0].relations
        ] == [("works_at", "outgoing")]
    else:
        assert hits[0].seed_entities == []
    env.graph.get_nodes.assert_not_awaited()
    env.graph.get_neighbors.assert_not_awaited()


def test_chunkhit_default_entity_lists_are_independent():
    first = ChunkHit("a", "r", "v", None, [], "text", 1, 1)
    second = ChunkHit("b", "r", "v", None, [], "text", 2, 1)
    assert first.seed_entities == [] and first.seed_entities is not second.seed_entities
