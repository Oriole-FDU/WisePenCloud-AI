from __future__ import annotations

from .engine import RankingEngine
from .fusion import WeightedRrfFusion
from .pipeline import RankingPipeline
from .rerankers import get_default_zero_entropy_reranker
from .scorers import BM25Scorer, FieldedBM25Scorer, FieldedBM25ScorerConfig
from .text import RankingTokenizer


class RankingEngineRegistry:
    """按名称提供已注册的 RankingEngine 单例。"""

    __slots__ = ("_engines",)

    def __init__(self) -> None:
        tokenizer = RankingTokenizer()
        reranker = get_default_zero_entropy_reranker()
        self._engines = {
            "services.ranked_expand": RankingEngine(
                pipeline=RankingPipeline(
                    name="services.ranked_expand",
                    scorers=(
                        BM25Scorer(tokenizer=tokenizer),
                        FieldedBM25Scorer(
                            tokenizer=tokenizer,
                            config=FieldedBM25ScorerConfig(
                                field_weights={"section": 2.0, "anchor": 1.5},
                            ),
                        ),
                    ),
                    fusion=WeightedRrfFusion(),
                    reranker=reranker,
                )
            ),
        }

    def get(self, name: str) -> RankingEngine:
        try:
            return self._engines[name]
        except KeyError as exc:
            raise KeyError(f"Unknown ranking engine: {name!r}") from exc


_REGISTRY = RankingEngineRegistry()


def get_ranking_engine(name: str) -> RankingEngine:
    return _REGISTRY.get(name)
