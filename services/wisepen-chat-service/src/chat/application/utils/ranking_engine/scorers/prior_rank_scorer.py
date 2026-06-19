from __future__ import annotations

from dataclasses import dataclass

from chat.application.utils.ranking_engine.models import (
    RankCandidate,
    RankQuery,
    ScoreSignal,
    ScoreSignalKind,
)


@dataclass(frozen=True, slots=True)
class PriorRankScorerConfig:
    """原始排名先验打分配置。"""

    signal_name: str = "prior_rank"
    k: float = 60.0 # 平滑度控制
    weight: float = 1.0


class PriorRankScorer:
    """根据 RankCandidate.prior_rank 生成先验排序信号。"""

    __slots__ = ("config", "name")

    def __init__(self, config: PriorRankScorerConfig | None = None) -> None:
        self.config = config or PriorRankScorerConfig()
        self.name = "prior_rank_scorer"

    def score(
        self,
        *,
        query: RankQuery,
        candidates: tuple[RankCandidate, ...],
    ) -> tuple[ScoreSignal, ...]:

        signals: list[ScoreSignal] = []
        cfg = self.config
        candidate_order = {
            candidate.candidate_id: index for index, candidate in enumerate(candidates)
        }

        for candidate in candidates:
            if candidate.prior_rank is None:
                continue

            signals.append(
                ScoreSignal(
                    candidate_id=candidate.candidate_id,
                    name=cfg.signal_name,
                    value=1.0 / (cfg.k + candidate.prior_rank),
                    kind=ScoreSignalKind.PRIOR,
                    rank=candidate.prior_rank,
                    weight=cfg.weight,
                    reason="Prior rank signal.",
                    metadata={
                        "scorer": self.name,
                        "k": cfg.k
                    },
                )
            )

        return tuple(
            sorted(
                signals,
                key=lambda signal: (
                    signal.rank if signal.rank is not None else len(candidates) + 1,
                    candidate_order[signal.candidate_id],
                ),
            )
        )

