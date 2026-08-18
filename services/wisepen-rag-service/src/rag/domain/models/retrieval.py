"""检索索引、召回请求和候选命中模型。"""

from dataclasses import dataclass, field, replace

from common.utils.document import SourceSpan


@dataclass(slots=True)
class RetrievalChunk:
    """评分使用的最小正文单位及其 ReadingBlock 归属。"""

    chunk_id: str
    reading_block_id: str
    section_id: str
    section_path: list[str]
    raw_text: str
    index_text: str
    source_spans: list[SourceSpan] = field(default_factory=list)
    page_labels: list[str] = field(default_factory=list)
    anchor_labels: list[str] = field(default_factory=list)

    def with_contextual_text(self, contextual_text: str) -> "RetrievalChunk":
        """返回只增强索引文本的副本，保留原文和证据坐标。"""
        return replace(
            self,
            index_text=f"Context: {contextual_text}\n\n{self.index_text}",
        )

@dataclass(slots=True)
class RetrievalCandidate:
    """Qdrant 返回的最小检索命中，等待 locate application 后续处理。"""

    chunk_id: str
    reading_block_id: str
    section_id: str
    section_path: list[str]
    resource_id: str
    content_revision: str
    raw_text: str
    source_spans: list[SourceSpan]
    page_labels: list[str]
    anchor_labels: list[str]
    source_ref_id: str
    score: float
