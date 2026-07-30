from __future__ import annotations

from chat.application.rag.graph_extraction import (
    ExtractedKnowledgeNode,
    ExtractedKnowledgeRelation,
    KnowledgeEntityType,
    KnowledgeEvidence,
    KnowledgeExtractionWindow,
    KnowledgeNodeKind,
    KnowledgeRelationType,
    KnowledgeWindowExtraction,
)
from chat.application.rag.graph_projection import (
    resource_node_id,
)
from chat.application.rag.graph_projection.projector import (
    build_knowledge_graph_projection,
)


def test_projection_resolves_exact_entities_and_keeps_revision_scoped_evidence() -> (
    None
):
    first = _extraction(
        chunk_id="chunk-1",
        alpha_label="Alpha",
        include_relation=True,
    )
    second = _extraction(
        chunk_id="chunk-2",
        alpha_label="alpha",
        include_relation=False,
    )

    projection = build_knowledge_graph_projection(
        resource_id="resource-1",
        content_revision="revision-1",
        extractions=(first, second),
    )

    resource = next(
        node for node in projection.nodes if node.kind is KnowledgeNodeKind.RESOURCE
    )
    alpha_nodes = [
        node
        for node in projection.nodes
        if node.entity_type is KnowledgeEntityType.PRODUCT
    ]
    assert resource.node_id == resource_node_id("resource-1")
    assert len(alpha_nodes) == 1
    assert alpha_nodes[0].label == "Alpha"
    assert len(projection.mentions) == 3
    assert {mention.chunk_id for mention in projection.mentions} == {
        "chunk-1",
        "chunk-2",
    }
    assert len(projection.edges) == 1
    edge = projection.edges[0]
    assert edge.relation_type is KnowledgeRelationType.DEPENDS_ON
    assert edge.evidence_quotes == ("evidence",)
    assert edge.evidence_source_ref_ids == ("source-chunk-1",)
    assert {mention.evidence_quote for mention in projection.mentions} == {"evidence"}
    assert edge.source_node_id == alpha_nodes[0].node_id
    assert all("chunk-1:" not in node.node_id for node in projection.nodes)

    repeated = build_knowledge_graph_projection(
        resource_id="resource-1",
        content_revision="revision-1",
        extractions=(first, second),
    )
    updated = build_knowledge_graph_projection(
        resource_id="resource-1",
        content_revision="revision-2",
        extractions=(first, second),
    )
    assert repeated == projection
    assert updated.relation_revision != projection.relation_revision
    assert updated.edges[0].edge_id != projection.edges[0].edge_id
def _extraction(
    *,
    chunk_id: str,
    alpha_label: str,
    include_relation: bool,
) -> KnowledgeWindowExtraction:
    window = KnowledgeExtractionWindow(
        resource_id="resource-1",
        document_version=1,
        content_revision="revision-1",
        chunk_id=chunk_id,
        chunk_index=0,
        current_text="Alpha depends on Beta.",
        source_mappings=(),
        source_refs=(),
    )
    alpha = ExtractedKnowledgeNode(
        local_id=f"{chunk_id}:alpha",
        kind=KnowledgeNodeKind.ENTITY,
        label=alpha_label,
        entity_type=KnowledgeEntityType.PRODUCT,
        evidence=_evidence(
            chunk_id=chunk_id,
            ref_id=f"evidence-{chunk_id}-alpha",
        ),
    )
    beta = ExtractedKnowledgeNode(
        local_id=f"{chunk_id}:beta",
        kind=KnowledgeNodeKind.ENTITY,
        label="Beta",
        entity_type=KnowledgeEntityType.TECHNOLOGY,
        evidence=_evidence(
            chunk_id=chunk_id,
            ref_id=f"evidence-{chunk_id}-beta",
        ),
    )
    relation = ExtractedKnowledgeRelation(
        source_local_id=alpha.local_id,
        target_local_id=beta.local_id,
        relation_type=KnowledgeRelationType.DEPENDS_ON,
        evidence=_evidence(
            chunk_id=chunk_id,
            ref_id=f"evidence-{chunk_id}-relation",
        ),
    )
    return KnowledgeWindowExtraction(
        window=window,
        nodes=(alpha, beta) if include_relation else (alpha,),
        relations=(relation,) if include_relation else (),
    )


def _evidence(
    *,
    chunk_id: str,
    ref_id: str,
) -> KnowledgeEvidence:
    return KnowledgeEvidence(
        evidence_ref_id=ref_id,
        source_ref_id=f"source-{chunk_id}",
        chunk_id=chunk_id,
        quote="evidence",
    )
