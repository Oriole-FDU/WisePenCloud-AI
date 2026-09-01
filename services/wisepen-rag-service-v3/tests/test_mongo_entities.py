"""P0 Mongo 实体必须声明的索引契约。"""

from rag_v3.domain.entities import (
    DocumentRevisionEntity,
    ResourceAclEntity,
    ResourceIndexStateEntity,
)


def _index_keys(entity_type) -> list[list[tuple[str, int]]]:
    return [list(index.document["key"].items()) for index in entity_type.Settings.indexes]


def test_p0_mongo_indexes_cover_visibility_acl_and_section_location() -> None:
    state_indexes = _index_keys(ResourceIndexStateEntity)
    acl_indexes = _index_keys(ResourceAclEntity)
    document_indexes = _index_keys(DocumentRevisionEntity)

    assert [("resource_id", 1)] in state_indexes
    assert [("resource_id", 1)] in acl_indexes
    assert [("resource_id", 1), ("content_revision", 1)] in document_indexes
    assert [("sections.section_id", 1)] in document_indexes
