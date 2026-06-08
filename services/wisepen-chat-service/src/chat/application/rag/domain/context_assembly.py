from dataclasses import dataclass
from typing import List


@dataclass(frozen=True, slots=True)
class RagAssembledContext:
    """RAG 组装后的上下文。

    将选中的证据列表组装为提供给 LLM 的最终上下文字符串，
    同时记录已包含和跳过的证据数量以便后续统计。
    """

    text: str
    included_evidence_ids: List[str]
    skipped_evidence_count: int
