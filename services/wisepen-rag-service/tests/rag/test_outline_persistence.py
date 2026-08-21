from beanie.odm.utils.encoder import Encoder
from common.utils.document import OutlineNode

from rag.core.persistence.mongo.published_resource_reader import _outline_node
from rag.core.persistence.mongo.resource_index_writer import _stored_outline_node
from rag.domain.entities import StoredOutlineNode


def test_outline_is_projected_to_beanie_encodable_models_recursively() -> None:
    node = OutlineNode(
        section_id="parent",
        title="Parent",
        length=10,
        children=[
            OutlineNode(
                section_id="child",
                title="Child",
                length=4,
                page_range="1",
                anchor_labels=["anchor"],
                children_truncated=True,
            )
        ],
    )

    stored = _stored_outline_node(node)
    encoded = Encoder().encode(stored)
    restored = _outline_node(StoredOutlineNode.model_validate(encoded))

    assert encoded["section_id"] == "parent"
    assert encoded["children"][0]["section_id"] == "child"
    assert restored == node
