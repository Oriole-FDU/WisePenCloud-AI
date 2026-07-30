from __future__ import annotations

from dataclasses import dataclass

from chat.application.rag.ingestion import RagSectionReadingBlock, RagSourceRef


@dataclass(frozen=True, slots=True)
class RagMaterializedSource:
    """回源后的 SourceRef，连带权威原文内容。"""

    source_ref: RagSourceRef  # 检索候选对应的 SourceRef 定位信息。
    content: str  # SourceRef 指向的原文文本，作为证据回源结果。


@dataclass(frozen=True, slots=True)
class RagMaterializedHit:
    """排序后候选与权威正文之间的最小交接模型。"""

    resource_id: str
    section_id: str
    reading_block: RagSectionReadingBlock  # 检索子块命中后回读的 Section 正文块。
    source: RagMaterializedSource  # 检索子块对应的精确证据。
