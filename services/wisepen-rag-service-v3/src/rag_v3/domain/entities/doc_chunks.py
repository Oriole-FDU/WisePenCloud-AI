"""DocChunk 的 Mongo 持久化实体。"""

from typing import ClassVar

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel

from .documents import StoredSpan


class StoredChunkMetadata(BaseModel):
    chunk_type: str = "general"


class DocChunkEntity(Document):
    """一个 revision 的检索原子和确定性增强占位值。"""

    chunk_id: str
    resource_id: str
    content_revision: str
    chunk_index: int
    section_id: str | None = None
    section_path: list[str] = Field(default_factory=list)
    raw_text: str
    source_spans: list[StoredSpan]
    page_labels: list[str] = Field(default_factory=list)
    anchor_labels: list[str] = Field(default_factory=list)
    contextual_prefix: str = ""
    key_terms: list[str] = Field(default_factory=list)
    extracted_node_ids: list[str] = Field(default_factory=list)
    metadata: StoredChunkMetadata = Field(default_factory=StoredChunkMetadata)

    class Settings:
        name = "doc_chunks"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("chunk_id", ASCENDING)],
                name="doc_chunk_id_unique",
                unique=True,
            ),
            IndexModel(
                [
                    ("resource_id", ASCENDING),
                    ("content_revision", ASCENDING),
                    ("chunk_index", ASCENDING),
                ],
                name="doc_chunk_revision_index_unique",
                unique=True,
            ),
            IndexModel(
                [
                    ("resource_id", ASCENDING),
                    ("content_revision", ASCENDING),
                    ("section_id", ASCENDING),
                    ("chunk_index", ASCENDING),
                ],
                name="doc_chunk_revision_section",
            ),
        ]
