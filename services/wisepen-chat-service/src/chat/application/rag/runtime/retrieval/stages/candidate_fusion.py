from dataclasses import dataclass
from typing import Dict, List

from chat.application.algorithms.ranking.rrf import RRF_K, RankedList, weighted_rrf
from chat.application.rag.runtime.retrieval.channels.models import (
    ChannelRetrievalResult,
    RagRetrievedCandidate,
)
from chat.application.rag.runtime.retrieval.enums import RetrievalChannel


@dataclass(frozen=True, slots=True)
class RagFusedCandidate:
    """RRF 融合后的候选项。

    将多路召回结果通过加权 RRF 算法融合为一个统一的排序列表。
    """

    candidate: RagRetrievedCandidate
    rrf_score: float
    rank: int
    sources: List[RetrievalChannel]


def _candidate_key(c: RagRetrievedCandidate) -> str:
    """生成候选在 RRF 融合中的唯一标识键。

    使用 (user_id, resource_kind, resource_id, index_version, chunk_id)
    五元组确保跨通道的相同 chunk 能被正确去重合并。
    """
    return f"{c.user_id}:{c.resource_kind.value}:{c.resource_id}:{c.index_version}:{c.chunk_id}"


class RagCandidateFusion:
    """多路召回候选融合器。

    接收来自 dense_semantic / sparse_lexical / keyword_exact 三个通道的检索结果，
    使用加权 Reciprocal Rank Fusion (RRF) 算法融合排序。
    """
    def __init__(self, rrf_k: int = RRF_K) -> None:
        """初始化融合器。

        Args:
            rrf_k: RRF 算法的常数 K，控制排名对融合分数的衰减速度。
        """
        self._rrf_k = rrf_k

    def fuse(
        self,
        *,
        channel_results: List[ChannelRetrievalResult],
        channel_weights: Dict[RetrievalChannel, float],
        top_k: int,
    ) -> List[RagFusedCandidate]:
        """融合多路召回结果为统一排序列表。

        算法流程：
        1. 收集所有候选，建立候选标识 → 候选对象的映射（用于跨通道去重）
        2. 对每个通道构建 RankedList（含通道权重）
        3. 调用加权 RRF 融合算法计算每个候选的融合分数
        4. 取 top_k 返回

        Args:
            channel_results: 各通道的检索结果列表。
            channel_weights: 当前检索计划启用通道的权重。
            top_k: 融合后保留的候选项数量。

        Returns:
            按 RRF 分数降序排列的融合候选项列表。

        Raises:
            ValueError: 通道结果包含不支持（未定义权重）的通道。
        """
        candidate_map: Dict[str, RagRetrievedCandidate] = {}
        for result in channel_results:
            for c in result.candidates:
                candidate_map.setdefault(_candidate_key(c), c)

        ranked_lists: List[RankedList] = []
        for i, result in enumerate(channel_results):
            if not result.candidates:
                continue
            if result.channel not in channel_weights:
                raise ValueError(f"Unsupported retrieval channel: {result.channel}")
            ranked_lists.append(
                RankedList(
                    name=result.channel.value,
                    ids=[_candidate_key(c) for c in result.candidates],
                    weight=channel_weights[result.channel],
                )
            )

        if not ranked_lists:
            return []

        rrf_items = weighted_rrf(ranked_lists=ranked_lists, k=self._rrf_k)

        return [
            RagFusedCandidate(
                candidate=candidate_map[item.id],
                rrf_score=item.score,
                rank=item.rank,
                sources=[RetrievalChannel(source) for source in item.sources],
            )
            for item in rrf_items[:top_k]
        ]
