from rag.application.rag.index.graph import merge_candidate_graph
from rag.application.rag.index.graph.extractor import KnowledgeWindowExtraction, ExtractedKnowledgeNode, \
    ExtractedKnowledgeRelation
from rag.domain.models.graph import (
    GraphEvidence,
    KnowledgeEntityType,
    KnowledgeNodeKind,
    KnowledgeRelationType,
)
from common.utils.document import SourceSpan


def test_merge_canonicalizes_unicode_case_and_builds_mentions() -> None:
    first = _extraction(
        reading_block_id="block-1",
        alpha_label="Ａlpha",
        evidence_id="alpha-1",
    )
    second = _extraction(
        reading_block_id="block-2",
        alpha_label="alpha",
        evidence_id="alpha-2",
    )

    graph = merge_candidate_graph(
        resource_id="resource-1",
        content_revision="revision-1",
        extractions=[first, second],
    )

    alpha_nodes = [
        node
        for node in graph.nodes
        if node.entity_type is KnowledgeEntityType.PRODUCT
    ]
    assert len(alpha_nodes) == 1
    assert alpha_nodes[0].label == "Alpha"
    assert len(graph.mentions) == 4
    assert {mention.evidence.reading_block_id for mention in graph.mentions} == {
        "block-1",
        "block-2",
    }


def test_merge_deduplicates_equivalent_relations_and_evidence_across_windows() -> (
    None
):
    first = _extraction(
        reading_block_id="block-1",
        alpha_label="Alpha",
        evidence_id="same-evidence",
    )
    second = _extraction(
        reading_block_id="block-1",
        alpha_label="alpha",
        evidence_id="same-evidence",
    )

    graph = merge_candidate_graph(
        resource_id="resource-1",
        content_revision="revision-1",
        extractions=[first, second],
    )

    assert len(graph.relations) == 1
    assert [
        item.quote for item in graph.relations[0].evidence
    ] == ["Alpha depends on Beta."]
    assert graph.relations[0].evidence[0].reading_block_id == "block-1"
    assert len(graph.mentions) == 2


def test_graph_revision_is_stable_and_changes_with_content_revision() -> None:
    first = _extraction(
        reading_block_id="block-1",
        alpha_label="Alpha",
        evidence_id="alpha-1",
    )
    second = _extraction(
        reading_block_id="block-2",
        alpha_label="alpha",
        evidence_id="alpha-2",
    )

    graph = merge_candidate_graph(
        resource_id="resource-1",
        content_revision="revision-1",
        extractions=[first, second],
    )
    reordered = merge_candidate_graph(
        resource_id="resource-1",
        content_revision="revision-1",
        extractions=[second, first],
    )
    updated = merge_candidate_graph(
        resource_id="resource-1",
        content_revision="revision-2",
        extractions=[
            _extraction(
                reading_block_id="block-1",
                alpha_label="Alpha",
                evidence_id="alpha-1",
                content_revision="revision-2",
            )
        ],
    )

    assert reordered == graph
    assert updated.graph_revision != graph.graph_revision


def _extraction(
    *,
    reading_block_id: str,
    alpha_label: str,
    evidence_id: str,
    content_revision: str = "revision-1",
) -> KnowledgeWindowExtraction:
    alpha_evidence = _evidence(
        evidence_id=f"{evidence_id}:alpha",
        reading_block_id=reading_block_id,
        quote="Alpha",
        content_revision=content_revision,
    )
    beta_evidence = _evidence(
        evidence_id=f"{evidence_id}:beta",
        reading_block_id=reading_block_id,
        quote="Beta",
        content_revision=content_revision,
    )
    relation_evidence = _evidence(
        evidence_id=f"{evidence_id}:relation",
        reading_block_id=reading_block_id,
        quote="Alpha depends on Beta.",
        content_revision=content_revision,
    )
    return KnowledgeWindowExtraction(
        resource_id="resource-1",
        content_revision=content_revision,
        nodes=[
            ExtractedKnowledgeNode(
                local_id=f"{reading_block_id}:{alpha_label}",
                kind=KnowledgeNodeKind.ENTITY,
                label=alpha_label,
                entity_type=KnowledgeEntityType.PRODUCT,
                evidence=alpha_evidence,
            ),
            ExtractedKnowledgeNode(
                local_id=f"{reading_block_id}:Beta",
                kind=KnowledgeNodeKind.ENTITY,
                label="Beta",
                entity_type=KnowledgeEntityType.TECHNOLOGY,
                evidence=beta_evidence,
            ),
        ],
        relations=[
            ExtractedKnowledgeRelation(
                source_local_id=f"{reading_block_id}:{alpha_label}",
                target_local_id=f"{reading_block_id}:Beta",
                relation_type=KnowledgeRelationType.DEPENDS_ON,
                evidence=relation_evidence,
            )
        ],
    )


def _evidence(
    *,
    evidence_id: str,
    reading_block_id: str,
    quote: str,
    content_revision: str,
) -> GraphEvidence:
    return GraphEvidence(
        evidence_id=evidence_id,
        resource_id="resource-1",
        content_revision=content_revision,
        reading_block_id=reading_block_id,
        source_span=SourceSpan(0, len(quote)),
        quote=quote,
    )
