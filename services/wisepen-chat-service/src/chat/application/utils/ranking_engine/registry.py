from __future__ import annotations

from zeroentropy import AsyncZeroEntropy

from chat.core.config.app_settings import settings
from .engine import RankingEngine
from .fusion import WeightedRrfFusion
from .pipeline import RankingPipeline
from .rerankers import ZeroEntropyReranker, ZeroEntropyRerankerConfig
from .scorers import BM25Scorer, FieldedBM25Scorer, FieldedBM25ScorerConfig
from .text import RankingTokenizer


class RankingEngineRegistry:
    """按名称提供已注册的 RankingEngine 单例。"""

    __slots__ = ("_engines",)

    def __init__(self) -> None:
        tokenizer = RankingTokenizer()
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
                )
            ),
            "session.evidence_rank": RankingEngine(
                pipeline=RankingPipeline(
                    name="session.evidence_rank",
                    reranker=ZeroEntropyReranker(
                        client=AsyncZeroEntropy(api_key=settings.ZERO_ENTROPY_API_KEY),
                        config=ZeroEntropyRerankerConfig(
                            model=settings.EVIDENCE_RANKER_ZE_MODEL,
                            top_n=settings.EVIDENCE_RANKER_ZE_TOP_N,
                        ),
                    ),
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
