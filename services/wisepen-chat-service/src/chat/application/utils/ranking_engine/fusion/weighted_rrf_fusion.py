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

            # 计算各信号 RRF 贡献：index → (rank, value)
            contributions: dict[int, tuple[int, float]] = {}
            for i, sig in enumerate(cand_signals):
                # 无排名候选跳过
                if sig.rank is None:
                    continue
                rank = sig.rank
                contributions[i] = (rank, sig.weight / (self.k + rank))

            score = sum(v for _, v in contributions.values())
            if score == 0.0:
                continue

            # 按贡献降序排列信号，分数相同则保持原次序
            sorted_indexed = tuple(
                sorted(
                    ((i, sig) for i, sig in enumerate(cand_signals) if i in contributions),
                    key=lambda x: (-contributions[x[0]][1], x[0]),
                )
            )

            # 生成 reason
            parts: list[str] = []
            for i, sig in sorted_indexed:
                rank, value = contributions[i]
                parts.append(f"{sig.name}@{rank}={value:.4f}")

            ranked_items.append(
                RankedCandidate(
                    candidate=candidate,
                    rank=0,
                    score=score,
                    signals=tuple(sig for _, sig in sorted_indexed),
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
