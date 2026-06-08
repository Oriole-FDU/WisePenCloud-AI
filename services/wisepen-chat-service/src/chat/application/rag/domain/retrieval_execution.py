from dataclasses import dataclass
from typing import List

from .answerability import SufficiencyResult
from .evidence_output import RagEvidence
from .retrieval_hits import ChannelRetrievalResult
from .retrieval_planning import RagRetrievalQuery


@dataclass(frozen=True, slots=True)
class RagRetrievalPipelineResult:
    """RAG 检索流水线结果。

    包含完整的检索流水线输出：
    - 原始查询参数
    - 各通道检索结果
    - 经过融合、重排、MMR 选择后的最终证据列表
    - 证据充分性判定结果
    """

    query: RagRetrievalQuery
    channel_results: List[ChannelRetrievalResult]
    evidences: List[RagEvidence]
    sufficiency: SufficiencyResult
