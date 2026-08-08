from __future__ import annotations

from dataclasses import dataclass

from ._utils import assign_ranks
from .core import RankDecision, RankGateResult, RankedCandidate


@dataclass(frozen=True, slots=True)
class HighLowRelevanceGateConfig:
    """高低水位相关性门控配置。"""

    low_watermark: float = 0.2  # 低于该分数的候选明确拒绝。
    high_watermark: float = 0.6  # 达到该分数的候选可作为相关结果。
    uncertain_limit: int = 3  # 灰区只保留少量候选供后续探索。

    def __post_init__(self) -> None:
        if not 0.0 <= self.low_watermark < self.high_watermark <= 1.0:
            raise ValueError(
                "relevance watermarks must satisfy "
                "0 <= low_watermark < high_watermark <= 1"
            )
        if self.uncertain_limit <= 0:
            raise ValueError("uncertain_limit must be positive")


class HighLowRelevanceGate:
    """按模型重排分数将候选集判定为相关、灰区或不相关。"""

    __slots__ = ("config",)

    def __init__(self, config: HighLowRelevanceGateConfig) -> None:
        self.config = config

    def evaluate(
        self,
        *,
        ranked: tuple[RankedCandidate, ...],
    ) -> RankGateResult:
        if not ranked:
            return RankGateResult(
                ranked=(),
                decision=RankDecision.IRRELEVANT,
                decision_score=None,
            )

        cfg = self.config
        decision_score = max(item.score for item in ranked)
        relevant = tuple(item for item in ranked if item.score >= cfg.high_watermark)
        if relevant:
            # 高水位命中时不混入灰区候选，避免弱证据稀释可靠结果。
            return RankGateResult(
                ranked=assign_ranks(relevant),
                decision=RankDecision.RELEVANT,
                decision_score=decision_score,
            )

        uncertain = tuple(item for item in ranked if item.score >= cfg.low_watermark)[
            : cfg.uncertain_limit
        ]
        if uncertain:
            return RankGateResult(
                ranked=assign_ranks(uncertain),
                decision=RankDecision.UNCERTAIN,
                decision_score=decision_score,
            )

        return RankGateResult(
            ranked=(),
            decision=RankDecision.IRRELEVANT,
            decision_score=decision_score,
        )
