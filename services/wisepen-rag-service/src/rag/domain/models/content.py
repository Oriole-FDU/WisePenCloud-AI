"""资源内容 revision 与可独立读取正文块的稳定领域模型。"""

from __future__ import annotations

from dataclasses import dataclass, field

from common.utils.document import SourceSpan


@dataclass(slots=True)
class ContentRevision:
    """一次内容发布的稳定身份与完整性校验元数据。"""

    resource_id: str
    content_revision: str
    document_version: int
    content_hash: str
    index_schema_version: str


@dataclass(slots=True)
class ReadingBlock:
    """覆盖一个或多个相邻兄弟 Section、且能精确回源的有序阅读块。"""

    block_id: str
    section_ids: list[str]
    ordinal: int
    raw_text: str
    source_spans: list[SourceSpan] = field(default_factory=list)
    page_labels: list[str] = field(default_factory=list)
    anchor_labels: list[str] = field(default_factory=list)
