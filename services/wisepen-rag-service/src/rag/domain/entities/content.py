"""文档与分块 Mongo 实体。"""

from beanie import Document as BeanieDocument
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from rag.domain.models.content import DocumentStructure


class DocumentEntity(BeanieDocument):
    """document_revisions 集合中的文档事实。"""

    resource_id: str
    document_version: int
    content_revision: str
    text: str
    structure: DocumentStructure
    metadata: dict[str, object] = Field(default_factory=dict)
    type: str | None = None

    class Settings:
        name = "document_revisions"
        indexes = [
            IndexModel(
                [("resource_id", ASCENDING), ("content_revision", ASCENDING)],
                name="document_resource_revision_unique",
                unique=True,
            ),
            IndexModel(
                [("structure.sections.section_id", ASCENDING)],
                name="document_section_id",
            ),
        ]


class ChunkEntity(BeanieDocument):
    """doc_chunks 集合中的分块事实。"""

    chunk_id: str
    resource_id: str
    content_revision: str
    chunk_index: int
    section_id: str | None
    section_path: list[str]
    raw_text: str
    content_token_count: int
    anchor_labels: list[str]
    metadata: dict[str, object] = Field(default_factory=dict)

    class Settings:
        name = "doc_chunks"
        indexes = [
            IndexModel(
                [("chunk_id", ASCENDING)], name="chunk_id_unique", unique=True
            ),
            IndexModel(
                [
                    ("resource_id", ASCENDING),
                    ("content_revision", ASCENDING),
                    ("chunk_index", ASCENDING),
                ],
                name="chunk_revision_index_unique",
                unique=True,
            ),
        ]
