"""P0 Mongo 实体必须声明的索引契约。"""

from rag_v3.domain.entities import (
    DocChunkEntity,
    DocumentRevisionEntity,
    GraphEdgeProjectionEntity,
    GraphNodeProjectionEntity,
    ResourceAclEntity,
    ResourceIndexStateEntity,
    TextGraphEvidenceEntity,
)


def _index_keys(entity_type) -> list[list[tuple[str, int]]]:
    return [list(index.document["key"].items()) for index in entity_type.Settings.indexes]


def test_mongo_indexes_cover_visibility_acl_document_and_chunk_location() -> None:
    state_indexes = _index_keys(ResourceIndexStateEntity)
    acl_indexes = _index_keys(ResourceAclEntity)
    document_indexes = _index_keys(DocumentRevisionEntity)
    chunk_indexes = _index_keys(DocChunkEntity)
    graph_node_indexes = _index_keys(GraphNodeProjectionEntity)
    graph_edge_indexes = _index_keys(GraphEdgeProjectionEntity)
    graph_evidence_indexes = _index_keys(TextGraphEvidenceEntity)

    assert [("resource_id", 1)] in state_indexes
    assert [("resource_id", 1)] in acl_indexes
    assert [("resource_id", 1), ("content_revision", 1)] in document_indexes
    assert [("sections.section_id", 1)] in document_indexes
    assert [("chunk_id", 1)] in chunk_indexes
    assert [
        ("resource_id", 1),
        ("content_revision", 1),
        ("chunk_index", 1),
    ] in chunk_indexes
    assert [
        ("resource_id", 1),
        ("content_revision", 1),
        ("node.node_id", 1),
        ("producer_id", 1),
    ] in graph_node_indexes
    assert [
        ("resource_id", 1),
        ("content_revision", 1),
        ("edge.edge_id", 1),
        ("producer_id", 1),
    ] in graph_edge_indexes
    assert [("evidence_id", 1)] in graph_evidence_indexes
    assert [
        ("resource_id", 1),
        ("content_revision", 1),
        ("section_id", 1),
        ("chunk_index", 1),
    ] in chunk_indexes


def test_doc_chunk_entity_does_not_persist_index_input_or_static_parent() -> None:
    fields = DocChunkEntity.model_fields

    assert "semantic_index_text" not in fields
    assert "lexical_index_text" not in fields
    assert "parent_raw_text" not in fields
