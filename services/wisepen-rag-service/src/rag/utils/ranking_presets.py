from zeroentropy import AsyncZeroEntropy

from common.utils.ranking import RankingPipeline
from common.utils.ranking.diversifiers import MmrDiversifier, MmrDiversifierConfig
from common.utils.ranking.fusion import WeightedRrfFusion
from common.utils.ranking.rerankers import (
    ZeroEntropyReranker,
    ZeroEntropyRerankerConfig,
)
from common.utils.ranking.scorers import (
    BM25Scorer,
    FieldedBM25Scorer,
    FieldedBM25ScorerConfig,
)
from common.utils.ranking.tokenizer import ThuLacRankingTokenizer
from rag.core.config.app_settings import settings

_THULAC_TOKENIZER = ThuLacRankingTokenizer()
_ZERO_ENTROPY_RERANKER = ZeroEntropyReranker(
    client=AsyncZeroEntropy(api_key=settings.ZERO_ENTROPY_API_KEY),
    config=ZeroEntropyRerankerConfig(model=settings.RERANKER_MODEL),
)

KNOWLEDGE_GRAPH_PATH_PIPELINE = RankingPipeline(
    scorers=(
        BM25Scorer(tokenizer=_THULAC_TOKENIZER),
        FieldedBM25Scorer(
            tokenizer=_THULAC_TOKENIZER,
            config=FieldedBM25ScorerConfig(
                field_weights={"nodes": 2.0, "relations": 2.0},
            ),
        ),
    ),
    fusion=WeightedRrfFusion(),
    reranker=_ZERO_ENTROPY_RERANKER,
)

KNOWLEDGE_SEARCH_PIPELINE = RankingPipeline(
    fusion=WeightedRrfFusion(),
    reranker=_ZERO_ENTROPY_RERANKER,
    diversifiers=(
        MmrDiversifier(
            tokenizer=_THULAC_TOKENIZER,
            config=MmrDiversifierConfig(
                lambda_mult=0.78,
                same_group_similarity=0.95,
            ),
        ),
    ),
)
