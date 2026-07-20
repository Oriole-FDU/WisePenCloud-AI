from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ._utils import assign_ranks
from .core import (
    Diversifier,
    Fusion,
    Prefilter,
    RankCandidate,
    RankedCandidate,
    RankRequest,
    RankResult,
    Reranker,
    Scorer,
    ScoreSignal,
)


@dataclass(frozen=True, slots=True)
class RankingPipeline:
    """按固定阶段编排一次排序，并直接提供同步和异步执行入口。"""

    prefilters: tuple[Prefilter, ...] = ()
    scorers: tuple[Scorer, ...] = ()
    fusion: Fusion | None = None
    reranker: Reranker | None = None
    diversifiers: tuple[Diversifier, ...] = ()

    def rank(self, request: RankRequest) -> RankResult:
        """同步执行优先过滤、打分融合和 MMR，不允许异步重排器。"""
        if self.reranker is not None:
            raise RuntimeError("Pipeline has async reranker; use arank().")

        ranked = self._rank_before_reranker(request)
        if not ranked or request.candidate_limit <= 0:
            return RankResult(
                ranked=(),
                total_candidates=len(request.candidates),
            )

        ranked = ranked[: request.candidate_limit]
        for diversifier in self.diversifiers:
            ranked = assign_ranks(diversifier.diversify(ranked=ranked))

        return RankResult(
            ranked=assign_ranks(ranked[: request.top_k]),
            total_candidates=len(request.candidates),
        )

    async def arank(self, request: RankRequest) -> RankResult:
        """执行完整排序流程，并在融合后调用可选异步重排器。"""
        ranked = await asyncio.to_thread(self._rank_before_reranker, request)
        if not ranked or request.candidate_limit <= 0:
            return RankResult(
                ranked=(),
                total_candidates=len(request.candidates),
            )

        ranked = ranked[: request.candidate_limit]
        if self.reranker is not None:
            ranked = await self.reranker.rerank(query=request.query, ranked=ranked)
            ranked = assign_ranks(ranked)[: request.candidate_limit]

        for diversifier in self.diversifiers:
            ranked = assign_ranks(
                await asyncio.to_thread(diversifier.diversify, ranked=ranked)
            )

        return RankResult(
            ranked=assign_ranks(ranked[: request.top_k]),
            total_candidates=len(request.candidates),
        )

    def _rank_before_reranker(
            self,
            request: RankRequest,
    ) -> tuple[RankedCandidate, ...]:
        """执行空请求判断、优先过滤以及初始排序。"""
        if request.top_k <= 0 or not request.candidates:
            return ()

        candidates = request.candidates
        for prefilter in self.prefilters:
            candidates = prefilter.prefilter(
                query=request.query,
                candidates=candidates,
            )
            if not candidates:
                return ()

        return self._build_initial_ranked(request=request, candidates=candidates)

    def _build_initial_ranked(
            self,
            *,
            request: RankRequest,
            candidates: tuple[RankCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:
        """使用外部信号、scorer 或输入顺序构造初始排名。"""
        if request.signals:
            if self.fusion is None:
                raise RuntimeError(
                    "RankRequest has external signals but pipeline has no fusion."
                )
            return assign_ranks(
                self.fusion.fuse(candidates=candidates, signals=request.signals)
            )

        if not self.scorers:
            return assign_ranks(
                tuple(
                    RankedCandidate(
                        candidate=candidate,
                        rank=0,
                        score=0.0,
                        reason="Seeded from input order without scorer.",
                        metadata={"initial_ranker": "input_order"},
                    )
                    for candidate in candidates
                )
            )

        if self.fusion is None:
            raise RuntimeError("Pipeline has scorers but no fusion.")

        signals: list[ScoreSignal] = []
        for scorer in self.scorers:
            signals.extend(scorer.score(query=request.query, candidates=candidates))
        return assign_ranks(
            self.fusion.fuse(candidates=candidates, signals=tuple(signals))
        )
