from zeroentropy import AsyncZeroEntropy

from chat.core.config.app_settings import settings

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
        model=settings.EVIDENCE_RANKER_ZE_MODEL,
        top_n=settings.EVIDENCE_RANKER_ZE_TOP_N,
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
