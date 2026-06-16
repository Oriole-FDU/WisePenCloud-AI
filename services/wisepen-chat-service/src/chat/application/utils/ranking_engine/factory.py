from __future__ import annotations

from .engine import RankingEngine

_ENGINES: dict[str, RankingEngine] = {}


def get_ranking_engine(name: str) -> RankingEngine:
    """按名称获取已注册的 RankingEngine 单例，首次访问时懒加载并缓存。

    Raises:
        KeyError: 名称未注册。
    """
    if (engine := _ENGINES.get(name)) is None:
        _ENGINES[name] = engine = _build_engine(name)
    return engine


def _build_engine(name: str) -> RankingEngine:
    # RankingEngine / RankingPipeline 所有分支都需要，统一提前
    from .core import RankingEngine, RankingPipeline

    match name:
        case "services.ranked_expand":
            from .fusion import WeightedRrfFusion
            from .scorers import BM25Scorer, FieldedBM25Scorer, FieldedBM25ScorerConfig
            from .text import RankingTokenizer

            tokenizer = RankingTokenizer()
            return RankingEngine(
                pipeline=RankingPipeline(
                    name=name,
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
            )

        case "session.evidence_rank":
            from chat.core.config.app_settings import settings
            from zeroentropy import AsyncZeroEntropy

            from .rerankers import ZeroEntropyReranker, ZeroEntropyRerankerConfig

            return RankingEngine(
                pipeline=RankingPipeline(
                    name=name,
                    reranker=ZeroEntropyReranker(
                        client=AsyncZeroEntropy(api_key=settings.ZERO_ENTROPY_API_KEY),
                        config=ZeroEntropyRerankerConfig(
                            model=settings.EVIDENCE_RANKER_ZE_MODEL,
                            top_n=settings.EVIDENCE_RANKER_ZE_TOP_N,
                        ),
                    ),
                )
            )

        case _:
            raise KeyError(f"Unknown ranking engine: {name!r}")