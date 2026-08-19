"""RAG 内容索引使用的 Beanie Mongo 文档实体。"""

from typing import ClassVar

from beanie import Document
from common.utils.document import OutlineNode
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel


class StoredSpan(BaseModel):
    start_offset: int
    end_offset: int


class StoredPage(BaseModel):
    page_index: int
    page_label: str
    start_offset: int
    end_offset: int


class StoredAnchor(BaseModel):
    label: str
    start_offset: int
    end_offset: int


class ResourceIndexStateEntity(Document):
    resource_id: str
    staged_content_revision: str | None = None
    staged_document_version: int | None = None
    applied_content_revision: str | None = None
    applied_document_version: int | None = None

    class Settings:
        name = "wisepen_rag_v2_resource_index_states"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("resource_id", ASCENDING)],
                name="idx_rag_v2_resource_index_state_resource",
                unique=True,
            )
        ]


class ContentRevisionEntity(Document):
    resource_id: str
    content_revision: str
    document_version: int
    content_hash: str
    index_schema_version: str
    structure_mode: str
    total_length: int
    outline: list[OutlineNode]
    pages: list[StoredPage] = Field(default_factory=list)
    anchors: list[StoredAnchor] = Field(default_factory=list)

    class Settings:
        name = "wisepen_rag_v2_content_revisions"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("content_revision", ASCENDING)],
                name="idx_rag_v2_content_revision",
                unique=True,
            ),
            IndexModel(
                [("resource_id", ASCENDING), ("document_version", ASCENDING)],
                name="idx_rag_v2_content_resource_version",
            ),
        ]


class SourcePartEntity(Document):
    resource_id: str
    content_revision: str
    part_index: int
    start_offset: int
    end_offset: int
    text: str

    class Settings:
        name = "wisepen_rag_v2_source_parts"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("content_revision", ASCENDING), ("part_index", ASCENDING)],
                name="idx_rag_v2_source_part_revision_order",
                unique=True,
            ),
            IndexModel(
                [("resource_id", ASCENDING), ("content_revision", ASCENDING)],
                name="idx_rag_v2_source_part_resource_revision",
            ),
        ]


class SectionEntity(Document):
    resource_id: str
    content_revision: str
    section_id: str
    title: str
    level: int
    parent_section_id: str | None = None
    ordinal: int
    section_path: list[str] = Field(default_factory=list)
    preview: str = ""
    own_start: int
    own_end: int
    subtree_end: int
    content_spans: list[StoredSpan]

    class Settings:
        name = "wisepen_rag_v2_sections"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("content_revision", ASCENDING), ("section_id", ASCENDING)],
                name="idx_rag_v2_section_revision_id",
                unique=True,
            ),
            IndexModel(
                [
                    ("content_revision", ASCENDING),
                    ("parent_section_id", ASCENDING),
                    ("ordinal", ASCENDING),
                ],
                name="idx_rag_v2_section_children",
            ),
            IndexModel(
                [
                    ("content_revision", ASCENDING),
                    ("own_start", ASCENDING),
                    ("own_end", ASCENDING),
                ],
                name="idx_rag_v2_section_range",
            ),
            IndexModel(
                [("resource_id", ASCENDING), ("content_revision", ASCENDING)],
                name="idx_rag_v2_section_resource_revision",
            ),
        ]


class ReadingBlockEntity(Document):
    resource_id: str
    content_revision: str
    block_id: str
    section_ids: list[str] = Field(default_factory=list)
    ordinal: int
    raw_text: str
    source_spans: list[StoredSpan] = Field(default_factory=list)
    start_offset: int
    end_offset: int
    page_labels: list[str] = Field(default_factory=list)
    anchor_labels: list[str] = Field(default_factory=list)

    class Settings:
        name = "wisepen_rag_v2_reading_blocks"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("content_revision", ASCENDING), ("block_id", ASCENDING)],
                name="idx_rag_v2_reading_block_revision_id",
                unique=True,
            ),
            IndexModel(
                [
                    ("content_revision", ASCENDING),
                    ("section_ids", ASCENDING),
                    ("ordinal", ASCENDING),
                ],
                name="idx_rag_v2_reading_block_sections_order",
            ),
            IndexModel(
                [
                    ("content_revision", ASCENDING),
                    ("start_offset", ASCENDING),
                    ("end_offset", ASCENDING),
                ],
                name="idx_rag_v2_reading_block_range",
            ),
            IndexModel(
                [("resource_id", ASCENDING), ("content_revision", ASCENDING)],
                name="idx_rag_v2_reading_block_resource_revision",
            ),
        ]


class SourceRefEntity(Document):
    resource_id: str
    content_revision: str
    ref_id: str
    chunk_id: str
    reading_block_id: str
    section_id: str
    section_path: list[str] = Field(default_factory=list)
    source_spans: list[StoredSpan] = Field(default_factory=list)
    page_labels: list[str] = Field(default_factory=list)
    anchor_labels: list[str] = Field(default_factory=list)

    class Settings:
        name = "wisepen_rag_v2_source_refs"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("content_revision", ASCENDING), ("ref_id", ASCENDING)],
                name="idx_rag_v2_source_ref_revision_id",
                unique=True,
            ),
            IndexModel(
                [("content_revision", ASCENDING), ("chunk_id", ASCENDING)],
                name="idx_rag_v2_source_ref_revision_chunk",
                unique=True,
            ),
            IndexModel(
                [
                    ("content_revision", ASCENDING),
                    ("reading_block_id", ASCENDING),
                ],
                name="idx_rag_v2_source_ref_reading_block",
            ),
            IndexModel(
                [("resource_id", ASCENDING), ("content_revision", ASCENDING)],
                name="idx_rag_v2_source_ref_resource_revision",
            ),
        ]
