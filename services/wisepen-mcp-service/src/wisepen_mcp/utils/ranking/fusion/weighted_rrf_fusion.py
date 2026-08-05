from __future__ import annotations

from .._utils import assign_ranks, candidate_positions
from ..core import RankCandidate, RankedCandidate, ScoreSignal

_RRF_K = 60.0


class WeightedRrfFusion:
    """按 signal.weight / (k + rank) 加权倒数排名融合多路信号。"""

    __slots__ = ()

    def fuse(
            self,
            *,
            candidates: tuple[RankCandidate, ...],
            signals: tuple[ScoreSignal, ...],
    ) -> tuple[RankedCandidate, ...]:
        if not candidates:
            return ()

        positions = candidate_positions(candidates)

        # 按 candidate_id 聚合信号，忽略未知候选
        grouped: dict[str, list[ScoreSignal]] = {
            candidate.candidate_id: [] for candidate in candidates
        }
        for signal in signals:
            if signal.candidate_id in grouped:
                grouped[signal.candidate_id].append(signal)

        ranked_items: list[RankedCandidate] = []

        for candidate in candidates:
            contributions: list[tuple[int, ScoreSignal, int, float]] = []
            for index, signal in enumerate(grouped[candidate.candidate_id]):
                if signal.rank is None:
                    continue
                contributions.append(
                    (
                        index,
                        signal,
                        signal.rank,
                        signal.weight / (_RRF_K + signal.rank),
                    )
                )

            if not contributions:
                continue

            # 按贡献降序排列信号，分数相同则保持原次序
            sorted_contributions = tuple(
                sorted(contributions, key=lambda item: (-item[3], item[0]))
            )

            ranked_items.append(
                RankedCandidate(
                    candidate=candidate,
                    rank=0,
                    score=sum(value for _, _, _, value in contributions),
                    signals=tuple(signal for _, signal, _, _ in sorted_contributions),
                    reason=", ".join(
                        f"{signal.name}@{rank}={value:.4f}"
                        for _, signal, rank, value in sorted_contributions
                    ),
                    metadata={"rrf_k": _RRF_K},
                )
            )

        ordered = sorted(
            ranked_items,
            key=lambda item: (-item.score, positions[item.candidate_id]),
        )
        return assign_ranks(tuple(ordered))
