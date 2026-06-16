from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvidenceRankItem:
    """一次 evidence_rank 输出的单条证据。"""

    content_id: str  # 实际读取并参与排序的 content_id
    chunk_index: int | None = None  # 分块序号；未分块内容为空
