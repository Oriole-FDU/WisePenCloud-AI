from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import numpy as np

from ..core.models import RankCandidate, RankedCandidate
from ._diversify_utils import assign_ranks, jaccard_similarity


@dataclass(frozen=True, slots=True)
class MaxMinDiversifierConfig:
    """轻量 Max-Min 多样性控制配置。"""

    diversity_weight: float = 0.35
    similarity_metadata_key: str = "embedding"
    use_embedding_similarity: bool = True
    use_text_similarity: bool = True
    max_candidates: int | None = None
    min_score: float | None = None
    reason: str = "max_min_diversified"


class MaxMinDiversifier:
    """基于贪心 Max-Min 算法的多样化器。

    原理：逐轮从剩余候选中选出「与已选集最不同且相关性最高」的候选，
          使最终结果在相关性和多样性之间取得平衡。
    """

    __slots__ = ("config", "name")

    def __init__(self, config: MaxMinDiversifierConfig | None = None) -> None:
        self.config = config or MaxMinDiversifierConfig()
        self.name = "max_min_diversifier"

    def diversify(
        self,
        *,
        ranked: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:

        if not ranked:
            return ()

        cfg = self.config
        max_candidates = (
            len(ranked)
            if cfg.max_candidates is None
            else min(max(cfg.max_candidates, 0), len(ranked))
        )
        # 只对 head 做多样化重排，tail 保持原顺序
        head = ranked[:max_candidates]
        tail = ranked[max_candidates:]
        diversity_weight = min(max(cfg.diversity_weight, 0.0), 1.0)
        # 原始分数归一化到 [0, 1]，作为相关性度量
        relevance_scores = self._normalize_scores(head)

        selected: list[RankedCandidate] = []
        remaining = list(head)

        # 贪心逐轮选择：每轮从 remaining 中挑出综合分最高的追加到 selected
        while remaining:
            best_index = 0
            best_score: float | None = None

            for index, item in enumerate(remaining):
                # 相关性分：原始排名归一化后的分数
                relevance = relevance_scores[item.candidate_id]
                if cfg.min_score is not None and item.score < cfg.min_score:
                    relevance = 0.0

                # 多样性分：1 - 与已选集中最相似候选的相似度
                # 第一轮 selected 为空，所有候选 diversity = 1.0，即只按相关性选
                if not selected:
                    diversity = 1.0
                else:
                    max_similarity = max(
                        self._similarity(item.candidate, selected_item.candidate)
                        for selected_item in selected
                    )
                    diversity = 1.0 - max_similarity

                # 综合分 = 加权求和，diversity_weight 越大越倾向多样性
                selection_score = (
                    (1.0 - diversity_weight) * relevance
                    + diversity_weight * diversity
                )
                if best_score is None or selection_score > best_score:
                    best_index = index
                    best_score = selection_score

            selected.append(remaining.pop(best_index))

        return assign_ranks(
            tuple(selected) + tail,
            reason_suffix="max_min",
            metadata_by_candidate_id={
                item.candidate_id: {
                    "diversifier": self.name,
                    "original_rank": item.rank,
                    "original_score": item.score,
                    "diversity_reason": self.config.reason,
                }
                for item in tuple(selected) + tail
            },
        )

    def _similarity(self, left: RankCandidate, right: RankCandidate) -> float:
        """计算两个候选的相似度，优先用 embedding 余弦相似度，fallback 到文本 Jaccard。"""
        cfg = self.config
        if cfg.use_embedding_similarity:
            left_embedding = left.metadata.get(cfg.similarity_metadata_key)
            right_embedding = right.metadata.get(cfg.similarity_metadata_key)
            if self._is_vector(left_embedding) and self._is_vector(right_embedding):
                return self._cosine_similarity(left_embedding, right_embedding)

        if cfg.use_text_similarity:
            return jaccard_similarity(
                set(self._select_text(left).lower().split()),
                set(self._select_text(right).lower().split()),
            )

        return 0.0

    @staticmethod
    def _is_vector(value: object) -> bool:
        """判断 metadata 中的值是否可视为数值向量。"""
        return isinstance(value, list | tuple) and all(
            isinstance(item, int | float) for item in value
        )

    @staticmethod
    def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
        """向量余弦相似度，shape 不匹配或含零向量时返回 0。"""
        left = np.asarray(a, dtype=np.float32)
        right = np.asarray(b, dtype=np.float32)
        if left.shape != right.shape:
            return 0.0
        dot = float(np.dot(left, right))
        norm_a = float(np.linalg.norm(left))
        norm_b = float(np.linalg.norm(right))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _select_text(candidate: RankCandidate) -> str:
        """提取候选的文本内容用于 Jaccard 相似度计算。"""
        if candidate.text.strip():
            return candidate.text
        return " ".join(value for value in candidate.fields.values() if value.strip())

    @staticmethod
    def _normalize_scores(items: tuple[RankedCandidate, ...]) -> dict[str, float]:
        """Min-max 归一化分数到 [0, 1]，全相等时全归 0。"""
        if not items:
            return {}

        min_score = min(item.score for item in items)
        max_score = max(item.score for item in items)
        if max_score == min_score:
            return {item.candidate_id: 0.0 for item in items}

        return {
            item.candidate_id: (item.score - min_score) / (max_score - min_score)
            for item in items
        }

    


