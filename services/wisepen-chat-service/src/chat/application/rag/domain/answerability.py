from dataclasses import dataclass
from typing import Dict, List, Optional

from chat.application.rag.domain.enums import (
    InsufficientReason,
    RetrievalChannel,
)
from chat.application.rag.domain.evidence_output import RagEvidence
from chat.application.rag.domain.retrieval_planning import RagRetrievalQuery
from chat.application.rag.enums import RetrievalMode

_MODE_MIN_SCORES: Dict[RetrievalMode, float] = {
    RetrievalMode.NORMAL: 0.15,
    RetrievalMode.SEMANTIC: 0.15,
    RetrievalMode.EXACT: 0.20,
}


@dataclass(slots=True)
class SufficiencyResult:
    """证据充分性判定结果。

    sufficient 为 True 表示检索结果足以进入下游环节；
    False 表示检索不充分，需触发重试或直接告知用户无法回答。
    """

    sufficient: bool
    reason: Optional[InsufficientReason] = None


class EvidenceSufficiencyEvaluator:
    """检索证据充分性评估器。

    根据检索模式（NORMAL / SEMANTIC / EXACT）对候选证据集合进行多维度判定：
    - 是否为空结果集
    - EXACT 模式下是否缺失 keyword_exact 命中
    - 最高重排得分是否低于模式对应阈值
    """
    def evaluate(
        self,
        *,
        query: RagRetrievalQuery,
        evidences: List[RagEvidence],
    ) -> SufficiencyResult:
        """评估当前检索结果是否充分。

        Args:
            query: 原始检索查询（含检索模式）。
            evidences: 经过重排和选择后的最终证据列表。

        Returns:
            SufficiencyResult: 判定结果以及不充分时的原因。
        """
        if not evidences:
            return SufficiencyResult(
                sufficient=False,
                reason=InsufficientReason.NO_RESULTS
            )

        # EXACT 模式下必须至少有一条来自 keyword_exact 通道的命中
        if query.mode == RetrievalMode.EXACT and not any(
            RetrievalChannel.KEYWORD_EXACT in ev.matched_channels for ev in evidences
        ):
            return SufficiencyResult(
                sufficient=False,
                reason=InsufficientReason.EXACT_MODE_NO_KEYWORD_HIT,
            )

        # 取所有证据的最高重排得分，与模式对应阈值比较
        min_score = _MODE_MIN_SCORES[query.mode]
        top_score = max(ev.rerank_score for ev in evidences)

        if top_score < min_score:
            return SufficiencyResult(
                sufficient=False,
                reason=InsufficientReason.LOW_SCORE
            )

        return SufficiencyResult(sufficient=True)
