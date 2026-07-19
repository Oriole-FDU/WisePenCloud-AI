from chat.core.config.app_settings import settings
from zeroentropy import AsyncZeroEntropy

from .diversifiers import MmrDiversifier, MmrDiversifierConfig
from .fusion import WeightedRrfFusion
from .pipeline import RankingPipeline
from .rerankers import ZeroEntropyReranker, ZeroEntropyRerankerConfig
from .scorers import BM25Scorer, FieldedBM25Scorer, FieldedBM25ScorerConfig
from .tokenizer import ThuLacRankingTokenizer

_THULAC_TOKENIZER = ThuLacRankingTokenizer()
_ZERO_ENTROPY_RERANKER = ZeroEntropyReranker(
    client=AsyncZeroEntropy(api_key=settings.ZERO_ENTROPY_API_KEY),
    config=ZeroEntropyRerankerConfig(
        model=settings.RERANKER_MODEL,
    ),
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

WEB_SEARCH_PIPELINE = RankingPipeline(
    scorers=(
        FieldedBM25Scorer(
            tokenizer=_THULAC_TOKENIZER,
            config=FieldedBM25ScorerConfig(
                field_weights={
                    "title": 3.0,
                    "overview": 1.5,
                    "highlights": 1.0,
                },
                min_score=-1.0,
            ),
        ),
    ),
    fusion=WeightedRrfFusion(),
    reranker=_ZERO_ENTROPY_RERANKER,
)
