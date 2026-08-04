from zeroentropy import AsyncZeroEntropy

from chat.application.utils.ranking import RankingPipeline
from chat.application.utils.ranking.fusion import WeightedRrfFusion
from chat.application.utils.ranking.rerankers import (
    ZeroEntropyReranker,
    ZeroEntropyRerankerConfig,
)
from chat.application.utils.ranking.scorers import (
    BM25Scorer,
    FieldedBM25Scorer,
    FieldedBM25ScorerConfig,
)
from chat.application.utils.ranking.tokenizer import ThuLacRankingTokenizer


def build_tool_content_semantic_search_pipeline() -> RankingPipeline:
    """构造工具内容窗口的词法检索和重排预设。"""
    from chat.core.config.app_settings import settings

    tokenizer = ThuLacRankingTokenizer()
    return RankingPipeline(
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
        reranker=ZeroEntropyReranker(
            client=AsyncZeroEntropy(api_key=settings.ZERO_ENTROPY_API_KEY),
            config=ZeroEntropyRerankerConfig(model=settings.RERANKER_MODEL),
        ),
    )
