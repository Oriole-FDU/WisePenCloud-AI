from __future__ import annotations

from typing import ClassVar

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel


class RagSourceSpanDocument(BaseModel):
    start_offset: int
    end_offset: int


class RagContentRevisionDocument(Document):
    content_revision: str
    resource_id: str
    document_version: int
    content_hash: str

    class Settings:
        name = "wisepen_rag_content_revisions"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("content_revision", ASCENDING)],
                name="idx_rag_content_revision",
                unique=True,
            ),
            IndexModel(
                [("resource_id", ASCENDING), ("document_version", ASCENDING)],
                name="idx_rag_content_resource_version",
            ),
        ]


class RagContentPartDocument(Document):
    content_revision: str
    part_index: int
    start_offset: int
    end_offset: int
    text: str

    class Settings:
        name = "wisepen_rag_content_parts"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("content_revision", ASCENDING), ("part_index", ASCENDING)],
                name="idx_rag_content_part_revision_order",
                unique=True,
            )
        ]


class RagSectionDocument(Document):
    content_revision: str
    section_id: str
    resource_id: str
    document_version: int
    title: str
    level: int
    parent_section_id: str | None = None
    ordinal: int
    section_path: list[str] = Field(default_factory=list)
    summary: str
    own_start: int
    own_end: int
    subtree_end: int

    class Settings:
        name = "wisepen_rag_sections"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("content_revision", ASCENDING), ("section_id", ASCENDING)],
                name="idx_rag_section_revision_id",
                unique=True,
            ),
            IndexModel(
                [
                    ("content_revision", ASCENDING),
                    ("parent_section_id", ASCENDING),
                    ("ordinal", ASCENDING),
                ],
                name="idx_rag_section_children",
            ),
        ]


class RagSectionReadingBlockDocument(Document):
    content_revision: str
    block_id: str
    section_id: str
    ordinal: int
    raw_text: str
    source_spans: list[RagSourceSpanDocument] = Field(min_length=1)
    page_labels: list[str] = Field(default_factory=list)
    anchor_labels: list[str] = Field(default_factory=list)

    class Settings:
        name = "wisepen_rag_section_reading_blocks"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("content_revision", ASCENDING), ("block_id", ASCENDING)],
                name="idx_rag_reading_block_revision_id",
                unique=True,
            ),
            IndexModel(
                [
                    ("content_revision", ASCENDING),
                    ("section_id", ASCENDING),
                    ("ordinal", ASCENDING),
                ],
                name="idx_rag_reading_block_section_order",
            ),
        ]


class RagSourceRefDocument(Document):
    content_revision: str
    ref_id: str
    resource_id: str
    document_version: int
    chunk_id: str
    section_id: str
    section_path: list[str] = Field(default_factory=list)
    source_spans: list[RagSourceSpanDocument] = Field(min_length=1)
    page_label: str | None = None
    anchor_labels: list[str] = Field(default_factory=list)

    class Settings:
        name = "wisepen_rag_source_refs"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("content_revision", ASCENDING), ("ref_id", ASCENDING)],
                name="idx_rag_source_ref_revision_id",
                unique=True,
            ),
            IndexModel(
                [("content_revision", ASCENDING), ("section_id", ASCENDING)],
                name="idx_rag_source_ref_section",
            ),
        ]


class RagProjectionCheckpointDocument(Document):
    resource_id: str
    staged_content_revision: str | None = None
    staged_document_version: int | None = None
    applied_content_revision: str | None = None
    applied_document_version: int | None = None

    class Settings:
        name = "wisepen_rag_projection_checkpoints"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("resource_id", ASCENDING)],
                name="idx_rag_projection_checkpoint_resource",
                unique=True,
            )
        ]
