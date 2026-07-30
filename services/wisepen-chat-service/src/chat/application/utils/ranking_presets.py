from zeroentropy import AsyncZeroEntropy

from chat.core.config.app_settings import settings
from common.utils.ranking import RankingPipeline
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

_THULAC_TOKENIZER = ThuLacRankingTokenizer()
_ZERO_ENTROPY_RERANKER = ZeroEntropyReranker(
    client=AsyncZeroEntropy(api_key=settings.ZERO_ENTROPY_API_KEY),
    config=ZeroEntropyRerankerConfig(model=settings.RERANKER_MODEL),
)

READ_RANKED_EXPAND_PIPELINE = RankingPipeline(
    scorers=(
        BM25Scorer(tokenizer=_THULAC_TOKENIZER),
        FieldedBM25Scorer(
            tokenizer=_THULAC_TOKENIZER,
            config=FieldedBM25ScorerConfig(
                field_weights={"section": 2.0, "anchor": 1.5},
            ),
        ),
    ),
    fusion=WeightedRrfFusion(),
    reranker=_ZERO_ENTROPY_RERANKER,
)
