"""图事实拓扑投影保留来源身份、去重结果和写入失败。"""

import copy
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag.domain.models import GraphEdge, GraphNode
from rag.ingestion.indexers import GraphIndexer


def facts(chunk):
    person = GraphNode(
        name="张三",
        source_id=chunk.chunk_id,
        ontology_name="work",
        category="Person",
        description="人员描述",
    )
    company = GraphNode(
        name="甲公司",
        source_id=chunk.chunk_id,
        ontology_name="work",
        category="Company",
    )
    edge = GraphEdge(
        source_id=person.id,
        target_id=company.id,
        relation_type="works_at",
        source_ids=[chunk.chunk_id],
        description="任职",
        metadata={"nested": {"value": 1}},
    )
    chunk.metadata.update(
        entities=[asdict(person), asdict(company)], relationships=[asdict(edge)]
    )
    return person, company, edge


async def test_source_projection_scope_dedup_and_readonly(graph_ontology, chunk):
    person, _, edge = facts(chunk)
    chunk.metadata["entities"].append(asdict(replace(person, description="不覆盖")))
    chunk.metadata["relationships"].append(asdict(replace(edge, description="不覆盖")))
    second = replace(
        chunk,
        chunk_id="second",
        resource_id="other",
        content_revision="other-revision",
        metadata={},
    )
    facts(second)
    before = copy.deepcopy([chunk, second])
    writer = SimpleNamespace(replace_sources=AsyncMock())
    assert (
            await GraphIndexer(ontology=graph_ontology, writer=writer).index(
                [chunk, second]
            )
            is None
    )
    assert [chunk, second] == before
    projections = writer.replace_sources.await_args.args[0]
    assert len(projections) == 2
    assert projections[0].metadata == {
        "resource_id": chunk.resource_id,
        "content_revision": chunk.content_revision,
        "ontology_name": graph_ontology.domain,
    }
    assert projections[1].metadata == {
        "resource_id": "other",
        "content_revision": "other-revision",
        "ontology_name": graph_ontology.domain,
    }
    assert len(projections[0].nodes) == 2 and len(projections[0].edges) == 1
    assert projections[0].nodes[0].description == "人员描述"
    assert projections[0].edges[0].description == "任职"
    assert projections[0].nodes[0].id == projections[1].nodes[0].id
    assert projections[0].nodes[0].source_id != projections[1].nodes[0].source_id


async def test_empty_success_is_explicit_replacement_and_writer_failure_propagates(
        graph_ontology, chunk
):
    chunk.metadata.update(entities=[], relationships=[])
    writer = SimpleNamespace(
        replace_sources=AsyncMock(side_effect=OSError("write failed"))
    )
    before = copy.deepcopy(chunk)
    with pytest.raises(OSError):
        await GraphIndexer(ontology=graph_ontology, writer=writer).index([chunk])
    projection = writer.replace_sources.await_args.args[0][0]
    assert (
            projection.chunk_id == chunk.chunk_id
            and projection.nodes == []
            and projection.edges == []
    )
    assert projection.metadata["ontology_name"] == graph_ontology.domain
    assert chunk == before


async def test_zero_chunks_no_io(graph_ontology):
    writer = SimpleNamespace(replace_sources=AsyncMock())
    await GraphIndexer(ontology=graph_ontology, writer=writer).index([])
    writer.replace_sources.assert_not_awaited()
