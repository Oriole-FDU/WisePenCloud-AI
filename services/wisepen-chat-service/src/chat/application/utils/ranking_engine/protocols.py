from __future__ import annotations

from typing import Protocol

from .models import (
    RankCandidate,
    RankedCandidate,
    RankQuery,
    ScoreSignal,
)


class Scorer(Protocol):
    """打分插件协议，只负责把候选转换成一组 ScoreSignal。

    典型实现：
    - BM25Scorer
    - FieldedBM25Scorer
    - KeywordExactScorer
    - OriginalRankPriorScorer
    - VectorSimilarityScorer
    """

    name: str  # 打分器名称

    def score(
        self,
        *,
        query: RankQuery,
        candidates: tuple[RankCandidate, ...],
    ) -> tuple[ScoreSignal, ...]:
        """计算候选排序信号。"""
        ...




class Fusion(Protocol):
    """融合插件协议，负责把多路 ScoreSignal 合成初始 RankedCandidate。

    典型实现：
    - WeightedSumFusion
    - WeightedRrfFusion
    - RuleBasedFusion
    """

    name: str  # 融合器名称

    def fuse(
        self,
        *,
        candidates: tuple[RankCandidate, ...],
        signals: tuple[ScoreSignal, ...],
    ) -> tuple[RankedCandidate, ...]:
        """融合多路排序信号，生成初始排序结果。"""
        ...


class Reranker(Protocol):
    """重排插件协议，负责基于查询和已有排序做二次排序。

    典型实现：
    - ZeroEntropyReranker
    """

    name: str  # 重排器名称

    async def rerank(
        self,
        *,
        query: RankQuery,
        ranked: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:
        """对已有排序结果进行二次重排。"""
        ...


class Diversifier(Protocol):
    """多样性控制插件协议，负责抑制重复来源或同组候选霸榜。

    典型实现：
    - MmrDiversifier
    - NearDedupDiversifier
    - GroupSuppressionDiversifier
    """

    name: str  # 多样性控制器名称

    def diversify(
        self,
        *,
        ranked: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:
        """对已有排序结果进行多样性控制。"""
        ...
