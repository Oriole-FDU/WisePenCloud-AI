from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .._utils import candidate_positions
from ..core import (
    RankCandidate,
    RankQuery,
    ScoreSignal,
    ScoreSignalKind,
)


@dataclass(frozen=True, slots=True)
class DenseVectorScorerConfig:
    """密集向量相似度打分配置。"""

    weight: float = 1.0  # 信号权重
    min_score: float = 0.0  # 最小保留分数


class DenseVectorScorer:
    """基于 query/candidate embedding 的通用 dense similarity scorer。"""

    __slots__ = ("config",)

    def __init__(
            self,
            *,
            config: DenseVectorScorerConfig | None = None,
    ) -> None:
        self.config = config or DenseVectorScorerConfig()

    def score(
            self,
            *,
            query: RankQuery,
            candidates: tuple[RankCandidate, ...],
    ) -> tuple[ScoreSignal, ...]:
        if not candidates:
            return ()

        positions = candidate_positions(candidates)

        cfg = self.config
        # query embedding 必须由上游放在 query.metadata["embedding"]。
        # scorer 不负责在线生成向量，避免排序阶段隐式发起模型调用。
        query_embedding = query.metadata.get("embedding")
        if query_embedding is None:
            raise ValueError('DenseVectorScorer requires query.metadata["embedding"].')

        scored: list[tuple[int, float, RankCandidate]] = []
        for candidate in candidates:
            # candidate embedding 也必须由召回/索引阶段准备好。
            candidate_embedding = candidate.metadata.get("embedding")
            if candidate_embedding is None:
                raise ValueError(
                    'DenseVectorScorer requires candidate.metadata["embedding"] '
                    f"for candidate_id={candidate.candidate_id}."
                )

            # dense scorer 的原始分就是 query 向量和 candidate 向量的 cosine similarity。
            # 后续 fusion 会再使用 ScoreSignal.weight 处理该信号的整体权重。
            score = _cosine(query_embedding, candidate_embedding)
            if score <= cfg.min_score:
                continue
            scored.append((positions[candidate.candidate_id], score, candidate))

        # 分数降序；同分时保持候选原始输入顺序稳定。
        scored.sort(key=lambda item: (-item[1], item[0]))

        return tuple(
            ScoreSignal(
                candidate_id=candidate.candidate_id,
                name="dense:cosine",
                value=float(score),
                kind=ScoreSignalKind.VECTOR,
                rank=rank,
                weight=cfg.weight,
                reason="Dense vector similarity.",
                metadata={
                    "metric": "cosine",
                },
            )
            for rank, (_, score, candidate) in enumerate(scored, 1)
        )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """计算 cosine similarity。"""
    left_array = np.asarray(left, dtype=np.float32)
    right_array = np.asarray(right, dtype=np.float32)
    if left_array.shape != right_array.shape:
        raise ValueError("Embedding dimensions do not match.")

    dot = float(np.dot(left_array, right_array))
    left_norm = float(np.linalg.norm(left_array))
    right_norm = float(np.linalg.norm(right_array))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
