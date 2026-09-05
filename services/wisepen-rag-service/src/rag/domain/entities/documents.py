"""Document revision 与 active 指针的 Mongo 实体。"""

from __future__ import annotations

from typing import Any, ClassVar

from beanie import Document
from common.utils.document import Anchor, Page, Section
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class ResourceIndexStateEntity(Document):
    """在线可见性只由该实体中的 applied 指针决定。"""

    resource_id: str
    staged_content_revision: str | None = None
    staged_document_version: int | None = None
    applied_content_revision: str | None = None

    class Settings:
        name = "resource_index_states"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("resource_id", ASCENDING)],
                name="resource_index_state_resource_unique",
                unique=True,
            ),
        ]


class DocumentRevisionEntity(Document):
    """一个 revision 的完整权威正文、结构和通用 metadata。"""

    resource_id: str
    content_revision: str
    document_version: int
    content_sha256: str
    raw_content: str
    total_length: int
    sections: list[Section] = Field(default_factory=list)
    pages: list[Page] = Field(default_factory=list)
    anchors: list[Anchor] = Field(default_factory=list)
    # metadata 由已注册的 Pydantic 类型解码，Mongo 只保存其原始结构。
    metadata: dict[str, Any] = Field(default_factory=dict)

    class Settings:
        name = "document_revisions"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("resource_id", ASCENDING), ("content_revision", ASCENDING)],
                name="document_revision_resource_revision_unique",
                unique=True,
            ),
            # 全局 Section ID 可先命中候选 revision；application 再核对 active 与 ACL。
            IndexModel(
                [("sections.section_id", ASCENDING)],
                name="document_revision_section_id",
            ),
        ]
