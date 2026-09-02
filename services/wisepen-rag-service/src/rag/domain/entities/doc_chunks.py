"""DocChunk 的 Mongo 持久化实体。"""

from typing import Any, ClassVar

from beanie import Document
from common.utils.document import SourceSpan
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class DocChunkEntity(Document):
    """一个 revision 的检索原子和确定性增强占位值。"""

    chunk_id: str
    resource_id: str
    content_revision: str
    chunk_index: int
    section_id: str | None = None
    section_path: list[str] = Field(default_factory=list)
    raw_text: str
    source_spans: list[SourceSpan]
    page_labels: list[str] = Field(default_factory=list)
    anchor_labels: list[str] = Field(default_factory=list)
    contextual_prefix: str = ""
    key_terms: list[str] = Field(default_factory=list)
    extracted_node_ids: list[str] = Field(default_factory=list)
    # Mongo 先保留完整对象，仓储再按注册表恢复具体 metadata 子类。
    metadata: dict[str, Any] = Field(default_factory=lambda: {"chunk_type": "general"})

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
