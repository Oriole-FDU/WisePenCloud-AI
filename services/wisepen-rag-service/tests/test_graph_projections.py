"""P2-B 图谱外部投影、版本和 ACL 边界测试。"""

from dataclasses import replace

import pytest

from rag.application.document.models import ResourceIndexState
from rag.application.graph.graph_indexing import GraphIndexBuilder
from rag.application.graph.models import (
    GraphEdge,
    GraphEdgeProjection,
    GraphNode,
    GraphNodeProjection,
    graph_edge_id,
    graph_node_id,
)
from rag.application.plugins.core.models import DocumentMetadata
from rag.core.persistence.neo4j.graph_topology_repository import _node_item
from rag.core.persistence.qdrant.graph_vector_repository import (
    QdrantGraphEdgeVectorRepository,
    QdrantGraphNodeVectorRepository,
    _edge_projection_id,
    _node_projection_id,
)
from rag.domain.acl import ResourceAcl
from rag.domain.repositories.graph_fact import GraphRevisionFacts

from .conftest import (
    MemoryAcls,
    MemoryDocChunks,
    MemoryDocuments,
    MemoryIndexStates,
    chunk_for_document,
    document,
)


class _GraphFacts:
    def __init__(self, facts: GraphRevisionFacts) -> None:
        self.facts = facts
        self.calls = 0

    async def get_revision_facts(self, **kwargs) -> GraphRevisionFacts:
        self.calls += 1
        return self.facts


class _PaperMetadata(DocumentMetadata):
    document_type: str = "paper"


class _Topology:
    def __init__(self) -> None:
        self.calls = []

    async def replace_revision(self, **kwargs) -> None:
        self.calls.append(kwargs)

    async def delete_resources(
        self, resource_ids
    ) -> None:  # pragma: no cover - protocol completeness
        pass


class _NodeVectors:
    def __init__(self) -> None:
        self.calls = []

    async def replace_revision(self, **kwargs) -> None:
        self.calls.append(kwargs)

    async def is_complete(
        self, **kwargs
    ) -> bool:  # pragma: no cover - protocol completeness
        return True

    async def delete_resources(
        self, resource_ids
    ) -> None:  # pragma: no cover - protocol completeness
        pass


class _EdgeVectors(_NodeVectors):
    pass


class _Embeddings:
    def __init__(self, vectors, on_create=None) -> None:
        self.vectors = vectors
        self.on_create = on_create

    async def create(self, **kwargs):
        if self.on_create is not None:
            self.on_create()
        return type(
            "Response",
            (),
            {
                "data": [
                    type("Item", (), {"embedding": vector}) for vector in self.vectors
                ]
            },
        )()


class _OpenAI:
    def __init__(self, embeddings) -> None:
        self.embeddings = embeddings


def _facts(item) -> GraphRevisionFacts:
    source = GraphNode(
        node_id=graph_node_id(category="paper", name="Source"),
        name="Source",
        category="paper",
    )
    target = GraphNode(
        node_id=graph_node_id(category="paper", name="Target"),
        name="Target",
        category="paper",
    )
    edge = GraphEdge(
        edge_id=graph_edge_id(
            source_node_id=source.node_id,
            relation_type="CITES",
            target_node_id=target.node_id,
        ),
        source_node_id=source.node_id,
        target_node_id=target.node_id,
        relation_type="CITES",
        keywords=("citation",),
    )
    values = {"reference_year": 2025}
    return GraphRevisionFacts(
        nodes=[
            GraphNodeProjection(
                node=source,
                resource_id=item.resource_id,
                content_revision=item.revision.content_revision,
                producer_id="paper-v1",
                filter_values=values,
            ),
            GraphNodeProjection(
                node=target,
                resource_id=item.resource_id,
                content_revision=item.revision.content_revision,
                producer_id="paper-v1",
                filter_values=values,
            ),
        ],
        edges=[
            GraphEdgeProjection(
                edge=edge,
                resource_id=item.resource_id,
                content_revision=item.revision.content_revision,
                producer_id="paper-v1",
                filter_values=values,
            ),
        ],
    )


async def _active_inputs(*, typed: bool = True):
    item = document(resource_id="paper", version=1, section_id="section")
    if typed:
        item = replace(item, metadata=_PaperMetadata())
    documents = MemoryDocuments()
    chunks = MemoryDocChunks()
    states = MemoryIndexStates()
    acl = ResourceAcl(resource_id="paper", acl_revision=3, owner_id="owner")
    await documents.save_revision(item)
    await chunks.save_revision([chunk_for_document(item)])
    await states.stage_revision(item.revision, expected_applied_content_revision=None)
    await states.apply_revision(item.revision)
    return item, documents, chunks, states, MemoryAcls({"paper": acl})


@pytest.mark.asyncio
async def test_disabled_projection_skips_before_loading_graph_facts() -> None:
    item, documents, chunks, states, acls = await _active_inputs()
    facts = _GraphFacts(_facts(item))
    builder = GraphIndexBuilder(
        enabled=False,
        documents=documents,
        doc_chunks=chunks,
        graph_facts=facts,
        resource_acls=acls,
        index_states=states,
        topology=None,
        node_vectors=_NodeVectors(),
        edge_vectors=_EdgeVectors(),
        openai_client=None,
        embedding_model="embedding",
        embedding_dimensions=2,
    )

    result = await builder.index(resource_id="paper")

    assert result.skipped is True
    assert facts.calls == 0


@pytest.mark.asyncio
async def test_general_document_skips_before_touching_projection_backends() -> None:
    item, documents, chunks, states, acls = await _active_inputs(typed=False)
    facts = _GraphFacts(_facts(item))
    topology = _Topology()
    nodes = _NodeVectors()
    edges = _EdgeVectors()
    builder = GraphIndexBuilder(
        enabled=True,
        documents=documents,
        doc_chunks=chunks,
        graph_facts=facts,
        resource_acls=acls,
        index_states=states,
        topology=topology,
        node_vectors=nodes,
        edge_vectors=edges,
        openai_client=None,
        embedding_model="embedding",
        embedding_dimensions=2,
    )

    result = await builder.index(resource_id="paper")

    assert result.skipped is True
    assert facts.calls == 0
    assert topology.calls == nodes.calls == edges.calls == []


@pytest.mark.asyncio
async def test_projection_writes_three_independent_targets_with_acl_and_filter_values() -> (
    None
):
    item, documents, chunks, states, acls = await _active_inputs()
    facts = _GraphFacts(_facts(item))
    topology = _Topology()
    nodes = _NodeVectors()
    edges = _EdgeVectors()
    builder = GraphIndexBuilder(
        enabled=True,
        documents=documents,
        doc_chunks=chunks,
        graph_facts=facts,
        resource_acls=acls,
        index_states=states,
        topology=topology,
        node_vectors=nodes,
        edge_vectors=edges,
        openai_client=_OpenAI(_Embeddings([[0.1, 0.2], [0.2, 0.3], [0.3, 0.4]])),
        embedding_model="embedding",
        embedding_dimensions=2,
    )

    result = await builder.index(resource_id="paper")

    assert (result.node_count, result.edge_count) == (2, 1)
    assert len(topology.calls) == len(nodes.calls) == len(edges.calls) == 1
    assert topology.calls[0]["resource_acl"].acl_revision == 3
    assert nodes.calls[0]["nodes"][0].filter_values == {"reference_year": 2025}
    assert set(edges.calls[0]["lexical_texts"].values()) == {
        "Source -> CITES -> Target\ncitation"
    }


@pytest.mark.asyncio
async def test_projection_rejects_missing_acl_and_active_switch_before_writing() -> (
    None
):
    item, documents, chunks, states, _ = await _active_inputs()
    facts = _GraphFacts(_facts(item))
    topology = _Topology()
    nodes = _NodeVectors()
    edges = _EdgeVectors()
    missing_acl = GraphIndexBuilder(
        enabled=True,
        documents=documents,
        doc_chunks=chunks,
        graph_facts=facts,
        resource_acls=MemoryAcls({}),
        index_states=states,
        topology=topology,
        node_vectors=nodes,
        edge_vectors=edges,
        openai_client=None,
        embedding_model="embedding",
        embedding_dimensions=2,
    )
    with pytest.raises(PermissionError, match="ACL"):
        await missing_acl.index(resource_id="paper")

    def switch_active() -> None:
        states.states["paper"] = ResourceIndexState(
            resource_id="paper",
            applied_content_revision="newer",
        )

    changing = GraphIndexBuilder(
        enabled=True,
        documents=documents,
        doc_chunks=chunks,
        graph_facts=facts,
        resource_acls=MemoryAcls({"paper": ResourceAcl("paper", 1, "owner")}),
        index_states=states,
        topology=topology,
        node_vectors=nodes,
        edge_vectors=edges,
        openai_client=_OpenAI(
            _Embeddings([[0.1, 0.2], [0.2, 0.3], [0.3, 0.4]], switch_active)
        ),
        embedding_model="embedding",
        embedding_dimensions=2,
    )
    with pytest.raises(ValueError, match="not active"):
        await changing.index(resource_id="paper")
    assert topology.calls == nodes.calls == edges.calls == []


def test_source_projection_identity_keeps_resources_separate() -> None:
    first = document(resource_id="first", version=1, section_id="section")
    second = document(resource_id="second", version=1, section_id="section")
    first_node = _facts(first).nodes[0].model_copy(update={"resource_id": "first"})
    second_node = _facts(second).nodes[0].model_copy(update={"resource_id": "second"})
    acl = ResourceAcl(resource_id="first", acl_revision=1, owner_id="owner")

    assert first_node.node.node_id == second_node.node.node_id
    assert _node_projection_id(first_node) != _node_projection_id(second_node)
    item = _node_item(first_node, acl)
    assert item["producer_id"] == "paper-v1"
    assert item["evidence_ids"] == []
    assert item["filter_properties"] == {"filter_reference_year": 2025}


class _Qdrant:
    def __init__(self) -> None:
        self.exists = False
        self.created = []
        self.indexes = []
        self.deletes = []
        self.upserts = []

    async def collection_exists(self, name):
        return self.exists

    async def create_collection(self, **kwargs):
        self.exists = True
        self.created.append(kwargs)

    async def create_payload_index(self, **kwargs):
        self.indexes.append(kwargs)

    async def delete(self, **kwargs):
        self.deletes.append(kwargs)

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)


@pytest.mark.asyncio
async def test_qdrant_node_and_edge_vectors_keep_two_index_shapes() -> None:
    item, _, _, _, acls = await _active_inputs()
    facts = _facts(item)
    acl = acls.values["paper"]
    client = _Qdrant()
    node_repository = QdrantGraphNodeVectorRepository(
        client=client,
        collection_name="nodes",
        dense_vector_size=2,
        dense_vector_name="dense",
    )
    node_vectors = {_node_projection_id(node): [0.1, 0.2] for node in facts.nodes}
    await node_repository.replace_revision(
        resource_id="paper",
        content_revision=item.revision.content_revision,
        nodes=facts.nodes,
        dense_vectors=node_vectors,
        resource_acl=acl,
    )
    assert "sparse_vectors_config" not in client.created[0]
    assert client.upserts[0]["points"][0].payload["owner_id"] == "owner"

    edge_client = _Qdrant()
    edge_repository = QdrantGraphEdgeVectorRepository(
        client=edge_client,
        collection_name="edges",
        dense_vector_size=2,
        dense_vector_name="dense",
        sparse_vector_name="sparse",
    )
    edge = facts.edges[0]
    edge_id = _edge_projection_id(edge)
    await edge_repository.replace_revision(
        resource_id="paper",
        content_revision=item.revision.content_revision,
        edges=facts.edges,
        dense_vectors={edge_id: [0.1, 0.2]},
        lexical_texts={edge_id: "Source -> CITES -> Target"},
        resource_acl=acl,
    )
    assert "sparse_vectors_config" in edge_client.created[0]
    assert (
        edge_client.upserts[0]["points"][0].vector["sparse"].text
        == "Source -> CITES -> Target"
    )
