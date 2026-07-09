from __future__ import annotations

from chat.application.utils.ranking_engine.models import RankCandidate, RankedCandidate, ScoreSignal


class WeightedRrfFusion:
    """按 signal.weight / (k + rank) 加权倒数排名融合多路信号。"""

    __slots__ = ("k", "name")

    def __init__(self, k: float = 60.0):
        self.k = k
        self.name = "weighted_rrf_fusion"

    def fuse(
            self,
            *,
            candidates: tuple[RankCandidate, ...],
            signals: tuple[ScoreSignal, ...],
    ) -> tuple[RankedCandidate, ...]:
        if not candidates:
            return ()

        # 构建原始顺序，检测重复 candidate_id
        candidate_order: dict[str, int] = {}
        for i, c in enumerate(candidates):
            if c.candidate_id in candidate_order:
                raise ValueError(f"Duplicate candidate_id: {c.candidate_id}")
            candidate_order[c.candidate_id] = i

        # 按 candidate_id 聚合信号，忽略未知候选
        grouped: dict[str, list[ScoreSignal]] = {c.candidate_id: [] for c in candidates}
        for sig in signals:
            if sig.candidate_id in grouped:
                grouped[sig.candidate_id].append(sig)

        ranked_items: list[RankedCandidate] = []

        for candidate in candidates:
            cand_signals = tuple(grouped[candidate.candidate_id])
            contributions: list[tuple[int, ScoreSignal, int, float]] = []
            for index, sig in enumerate(cand_signals):
                if sig.rank is None:
                    continue
                contributions.append(
                    (index, sig, sig.rank, sig.weight / (self.k + sig.rank))
                )

            if not contributions:
                continue

            # 按贡献降序排列信号，分数相同则保持原次序
            sorted_contributions = tuple(
                sorted(contributions, key=lambda item: (-item[3], item[0]))
            )

            # 生成 reason
            parts: list[str] = []
            for _, sig, rank, value in sorted_contributions:
                parts.append(f"{sig.name}@{rank}={value:.4f}")

            ranked_items.append(
                RankedCandidate(
                    candidate=candidate,
                    rank=0,
                    score=sum(value for _, _, _, value in contributions),
                    signals=tuple(sig for _, sig, _, _ in sorted_contributions),
                    reason=", ".join(parts),
                    metadata={"fusion": self.name, "rrf_k": self.k},
                )
            )

        ordered = sorted(
            ranked_items,
            key=lambda item: (-item.score, candidate_order[item.candidate_id]),
        )

        return tuple(
            RankedCandidate(
                candidate=item.candidate,
                rank=index,
                score=item.score,
                signals=item.signals,
                reason=item.reason,
                metadata=item.metadata,
            )
            for index, item in enumerate(ordered, 1)
        )
