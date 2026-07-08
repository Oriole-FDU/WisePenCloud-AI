from __future__ import annotations

from .diversifiers import MmrDiversifier, MmrDiversifierConfig
from .engine import RankingEngine
from .fusion import WeightedRrfFusion
from .pipeline import RankingPipeline
from .rerankers import get_default_zero_entropy_reranker
from .scorers import (
    BM25Scorer,
    FieldedBM25Scorer,
    FieldedBM25ScorerConfig,
)
from .tokenizer import JiebaRankingTokenizer, ThuLacRankingTokenizer


class RankingEngineRegistry:
    """按名称提供已注册的 RankingEngine 单例。"""

    __slots__ = ("_engines", "_tokenizers")

    def __init__(self) -> None:
        self._tokenizers = {
            "jieba": JiebaRankingTokenizer(),
            "thulac": ThuLacRankingTokenizer(),
        }
        reranker = get_default_zero_entropy_reranker()
        self._engines = {
            "read.ranked_expand": RankingEngine(
                pipeline=RankingPipeline(
                    name="read.ranked_expand",
                    scorers=(
                        BM25Scorer(tokenizer=self._tokenizers["thulac"]),  # 全文打分
                        FieldedBM25Scorer(  # section，achor 命中额外加分
                            tokenizer=self._tokenizers["thulac"],
                            config=FieldedBM25ScorerConfig(
                                field_weights={"section": 2.0, "anchor": 1.5},
                            ),
                        ),
                    ),
                    fusion=WeightedRrfFusion(),
                    reranker=reranker,
                )
            ),
            "rag.knowledge_search": RankingEngine(
                pipeline=RankingPipeline(
                    name="rag.knowledge_search",
                    fusion=WeightedRrfFusion(),
                    reranker=reranker,
                    diversifiers=(
                        MmrDiversifier(
                            tokenizer=self._tokenizers["thulac"],
                            config=MmrDiversifierConfig(
                                lambda_mult=0.78,
                                same_group_similarity=0.95,
                            ),
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
