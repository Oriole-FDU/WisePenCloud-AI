from zeroentropy import AsyncZeroEntropy

from rag.utils.ranking import RankingPipeline
from rag.utils.ranking.diversifiers import MmrDiversifier, MmrDiversifierConfig
from rag.utils.ranking.fusion import WeightedRrfFusion
from rag.utils.ranking.rerankers import (
    ZeroEntropyReranker,
    ZeroEntropyRerankerConfig,
)
from rag.utils.ranking.scorers import (
    BM25Scorer,
    FieldedBM25Scorer,
    FieldedBM25ScorerConfig,
)
from rag.utils.ranking.tokenizer import ThuLacRankingTokenizer


def build_knowledge_graph_path_ranking_pipeline() -> RankingPipeline:
    """构造知识图谱路径检索的词法匹配与重排预设。"""
    tokenizer = ThuLacRankingTokenizer()
    return RankingPipeline(
        scorers=(
            BM25Scorer(tokenizer=tokenizer),
            FieldedBM25Scorer(
                tokenizer=tokenizer,
                config=FieldedBM25ScorerConfig(
                    field_weights={"nodes": 2.0, "relations": 2.0},
                ),
            ),
        ),
        fusion=WeightedRrfFusion(),
        reranker=_build_zero_entropy_reranker(),
    )


def build_knowledge_search_ranking_pipeline() -> RankingPipeline:
    """构造知识检索的融合、重排和去重预设。"""
    tokenizer = ThuLacRankingTokenizer()
    return RankingPipeline(
        fusion=WeightedRrfFusion(),
        reranker=_build_zero_entropy_reranker(),
        diversifiers=(
            MmrDiversifier(
                tokenizer=tokenizer,
                config=MmrDiversifierConfig(
                    lambda_mult=0.78,
                    same_group_similarity=0.95,
                ),
            ),
        ),
    )


def _build_zero_entropy_reranker() -> ZeroEntropyReranker:
    from rag.core.config.app_settings import settings

    return ZeroEntropyReranker(
        client=AsyncZeroEntropy(api_key=settings.ZERO_ENTROPY_API_KEY),
        config=ZeroEntropyRerankerConfig(model=settings.RERANKER_MODEL),
    )
