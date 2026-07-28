from __future__ import annotations

from dataclasses import replace

import pytest

from chat.application.rag.graph_extraction import (
    ExtractedKnowledgeNode,
    ExtractedKnowledgeRelation,
    KnowledgeAssertion,
    KnowledgeEntityType,
    KnowledgeEvidence,
    KnowledgeExtractionWindow,
    KnowledgeNodeKind,
    KnowledgeRelationProfile,
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
        alpha_offset=10,
        include_relation=True,
    )
    second = _extraction(
        chunk_id="chunk-2",
        alpha_label="alpha",
        alpha_offset=100,
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
    assert alpha_nodes[0].canonical_key == "alpha"
    assert alpha_nodes[0].label == "Alpha"
    assert len(projection.mentions) == 3
    assert {mention.chunk_id for mention in projection.mentions} == {
        "chunk-1",
        "chunk-2",
    }
    assert len(projection.edges) == 1
    edge = projection.edges[0]
    assert edge.relation_type is KnowledgeRelationType.DEPENDS_ON
    assert edge.relation_profile is KnowledgeRelationProfile.CORE
    assert edge.evidence_ref_ids == ("evidence-chunk-1-relation",)
    assert edge.evidence_source_ref_ids == ("source-chunk-1",)
    assert edge.assertions == (KnowledgeAssertion.AFFIRMED,)
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


def test_projection_preserves_assertions_for_the_same_evidence() -> None:
    extraction = _extraction(
        chunk_id="chunk-1",
        alpha_label="Alpha",
        alpha_offset=10,
        include_relation=True,
    )
    uncertain = replace(
        extraction.relations[0],
        assertion=KnowledgeAssertion.UNCERTAIN,
    )
    extraction = replace(
        extraction,
        relations=(*extraction.relations, uncertain),
    )

    projection = build_knowledge_graph_projection(
        resource_id="resource-1",
        content_revision="revision-1",
        extractions=(extraction,),
    )

    edge = projection.edges[0]
    assert edge.evidence_ref_ids == (
        "evidence-chunk-1-relation",
        "evidence-chunk-1-relation",
    )
    assert edge.assertions == (
        KnowledgeAssertion.AFFIRMED,
        KnowledgeAssertion.UNCERTAIN,
    )


def test_projection_rejects_conflicting_relation_profiles() -> None:
    extraction = _extraction(
        chunk_id="chunk-1",
        alpha_label="Alpha",
        alpha_offset=10,
        include_relation=True,
    )
    conflicting = replace(
        extraction.relations[0],
        relation_profile=KnowledgeRelationProfile.LEARNING,
    )
    extraction = replace(
        extraction,
        relations=(*extraction.relations, conflicting),
    )

    with pytest.raises(ValueError, match="conflicting profiles"):
        build_knowledge_graph_projection(
            resource_id="resource-1",
            content_revision="revision-1",
            extractions=(extraction,),
        )


def _extraction(
    *,
    chunk_id: str,
    alpha_label: str,
    alpha_offset: int,
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
            start=alpha_offset,
            end=alpha_offset + 5,
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
            start=alpha_offset + 17,
            end=alpha_offset + 21,
        ),
    )
    relation = ExtractedKnowledgeRelation(
        source_local_id=alpha.local_id,
        target_local_id=beta.local_id,
        relation_type=KnowledgeRelationType.DEPENDS_ON,
        relation_profile=KnowledgeRelationProfile.CORE,
        assertion=KnowledgeAssertion.AFFIRMED,
        evidence=_evidence(
            chunk_id=chunk_id,
            ref_id=f"evidence-{chunk_id}-relation",
            start=alpha_offset,
            end=alpha_offset + 22,
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
    start: int,
    end: int,
) -> KnowledgeEvidence:
    return KnowledgeEvidence(
        evidence_ref_id=ref_id,
        source_ref_id=f"source-{chunk_id}",
        resource_id="resource-1",
        document_version=1,
        chunk_id=chunk_id,
        start_offset=start,
        end_offset=end,
        quote="evidence",
        page_label=None,
        section_id="section-1",
        section_path=("章节",),
    )
