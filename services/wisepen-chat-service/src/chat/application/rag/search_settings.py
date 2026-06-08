from dataclasses import dataclass
from typing import Dict, List

from chat.application.rag.enums import RetrievalMode
from chat.application.rag.runtime.retrieval.enums import RetrievalChannel


@dataclass(frozen=True, slots=True)
class RagSearchSettings:
    """RAG 检索配置。

    统一承载 mode 对检索执行、融合、重排和上下文扩展的影响。
    """

    top_k: int
    fusion_top_k: int
    rerank_top_n: int
    final_top_k: int
    neighbor_before: int
    neighbor_after: int
    mmr_lambda: float
    semantic_query_limit: int
    keyword_query_limit: int
    active_channels: List[RetrievalChannel]
    channel_weights: Dict[RetrievalChannel, float]


_DEFAULT_SETTINGS = {
    RetrievalMode.NORMAL: RagSearchSettings(
        top_k=30,
        fusion_top_k=50,
        rerank_top_n=30,
        final_top_k=8,
        neighbor_before=1,
        neighbor_after=1,
        mmr_lambda=0.72,
        semantic_query_limit=3,
        keyword_query_limit=3,
        active_channels=[
            RetrievalChannel.DENSE_SEMANTIC,
            RetrievalChannel.SPARSE_LEXICAL,
            RetrievalChannel.KEYWORD_EXACT,
        ],
        channel_weights={
            RetrievalChannel.DENSE_SEMANTIC: 1.0,
            RetrievalChannel.SPARSE_LEXICAL: 1.0,
            RetrievalChannel.KEYWORD_EXACT: 1.0,
        },
    ),
    RetrievalMode.SEMANTIC: RagSearchSettings(
        top_k=40,
        fusion_top_k=60,
        rerank_top_n=30,
        final_top_k=8,
        neighbor_before=1,
        neighbor_after=1,
        mmr_lambda=0.78,
        semantic_query_limit=5,
        keyword_query_limit=2,
        active_channels=[
            RetrievalChannel.DENSE_SEMANTIC,
            RetrievalChannel.SPARSE_LEXICAL,
        ],
        channel_weights={
            RetrievalChannel.DENSE_SEMANTIC: 1.1,
            RetrievalChannel.SPARSE_LEXICAL: 1.0,
        },
    ),
    RetrievalMode.EXACT: RagSearchSettings(
        top_k=40,
        fusion_top_k=70,
        rerank_top_n=30,
        final_top_k=8,
        neighbor_before=0,
        neighbor_after=0,
        mmr_lambda=0.65,
        semantic_query_limit=2,
        keyword_query_limit=5,
        active_channels=[
            RetrievalChannel.SPARSE_LEXICAL,
            RetrievalChannel.KEYWORD_EXACT,
        ],
        channel_weights={
            RetrievalChannel.SPARSE_LEXICAL: 1.0,
            RetrievalChannel.KEYWORD_EXACT: 1.1,
        },
    ),
}


def get_default_search_settings(mode: RetrievalMode) -> RagSearchSettings:
    """返回指定检索模式的默认配置。"""
    return _DEFAULT_SETTINGS[mode]
