"""检索与图谱共同使用的权威来源模型。"""

from dataclasses import dataclass, field

from common.utils.document import SourceSpan

from rag.domain.models.content import ReadingBlock
from rag.domain.models.structure import Section


@dataclass(slots=True)
class SourceRef:
    """RetrievalChunk 到特定已发布 revision 原文的稳定引用。"""

    ref_id: str
    resource_id: str
    content_revision: str
    chunk_id: str
    reading_block_id: str
    section_id: str
    section_path: list[str]
    source_spans: list[SourceSpan] = field(default_factory=list)
    page_labels: list[str] = field(default_factory=list)
    anchor_labels: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SourceEvidence:
    """按 SourceRef 从当前已发布资源解析出的权威证据。"""

    source_ref: SourceRef
    reading_block: ReadingBlock
    section: Section
    source_text: str
    # 与 reading_block.section_ids 同序，用于把多 Section 父块投影为可读文本。
    reading_block_sections: list[Section] = field(default_factory=list)
