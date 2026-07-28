from __future__ import annotations

from dataclasses import dataclass

from chat.application.rag.ingestion import RagSectionReadingBlock, RagSourceRef
from chat.application.rag.retrieval import RagRankedHit


@dataclass(frozen=True, slots=True)
class RagMaterializedSource:
    """回源后的 SourceRef，连带权威原文内容。"""

    source_ref: RagSourceRef  # 检索候选对应的 SourceRef 定位信息。
    content: str  # SourceRef 指向的原文文本，作为证据回源结果。


@dataclass(frozen=True, slots=True)
class RagMaterializedHit:
    """回源后的检索命中，附带完整证据链。"""

    hit: RagRankedHit  # 经过排序的最终命中。
    reading_block: RagSectionReadingBlock  # 检索子块命中后回读的 Section 正文块。
    source: RagMaterializedSource  # 检索子块对应的精确证据。

    @property
    def resource_id(self) -> str:
        return self.hit.candidate.resource_id

    @property
    def chunk_id(self) -> str:
        return self.hit.candidate.chunk_id
