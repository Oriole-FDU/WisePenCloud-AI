from wisepen_mcp.utils.ranking import RankingPipeline
from wisepen_mcp.utils.ranking.fusion import WeightedRrfFusion
from wisepen_mcp.utils.ranking.rerankers import (
    ZeroEntropyReranker,
    ZeroEntropyRerankerConfig,
)
from wisepen_mcp.utils.ranking.scorers import FieldedBM25Scorer, FieldedBM25ScorerConfig
from wisepen_mcp.utils.ranking.tokenizer import ThuLacRankingTokenizer
from zeroentropy import AsyncZeroEntropy


def build_web_search_ranking_pipeline() -> RankingPipeline:
    from wisepen_mcp.core.config.app_settings import settings

    reranker = None
    if settings.ZERO_ENTROPY_API_KEY:
        reranker = ZeroEntropyReranker(
            client=AsyncZeroEntropy(api_key=settings.ZERO_ENTROPY_API_KEY),
            config=ZeroEntropyRerankerConfig(model=settings.RERANKER_MODEL),
        )

    return RankingPipeline(
        scorers=(
            FieldedBM25Scorer(
                tokenizer=ThuLacRankingTokenizer(),
                config=FieldedBM25ScorerConfig(
                    field_weights={
                        "title": 3.0,
                        "snippet": 1.5,
                        "highlights": 1.0,
                    },
                    min_score=-1.0,
                ),
            ),
        ),
        fusion=WeightedRrfFusion(),
        reranker=reranker,
    )
