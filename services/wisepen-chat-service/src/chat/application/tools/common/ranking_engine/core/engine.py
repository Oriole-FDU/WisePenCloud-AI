from __future__ import annotations

from .models import RankedCandidate, RankRequest, RankResult, ScoreSignal
from .pipeline import RankingPipeline


class RankingEngine:
    """排序引擎，负责按 pipeline 编排 scorer、fusion、reranker 和 diversifier。"""

    def __init__(self, *, pipeline: RankingPipeline) -> None:
        self._pipeline = pipeline

    def rank(self, request: RankRequest) -> RankResult:
        """同步执行一次排序请求（不含异步 reranker）。"""
        pipeline = self._pipeline
        if pipeline.reranker is not None:
            raise RuntimeError("Pipeline has async reranker; use rank_async().")

        # 空请求直接返回
        if request.top_k <= 0 or not request.candidates:
            return RankResult(
                ranked=(),
                total_candidates=len(request.candidates),
                pipeline=pipeline.name,
            )

        # 1. 所有 scorer 打分，产出 ScoreSignal
        signals = self._collect_signals(request=request, pipeline=pipeline)

        # 2. fusion 融合信号，得到候选初始分
        ranked = pipeline.fusion.fuse(
            candidates=request.candidates,
            signals=signals,
        )
        ranked = self._assign_rank(ranked)

        if request.candidate_limit <= 0:
            return RankResult(
                ranked=(),
                total_candidates=len(request.candidates),
                pipeline=pipeline.name,
            )

        # 3. candidate_limit 截断，减少后续阶段计算量
        ranked = ranked[: request.candidate_limit]

        # 4. 多样性控制
        if pipeline.diversifier is not None:
            ranked = pipeline.diversifier.diversify(ranked=ranked)
            ranked = self._assign_rank(ranked)

        # 5. top_k 截断，最终输出
        ranked = self._assign_rank(ranked[: request.top_k])

        return RankResult(
            ranked=ranked,
            total_candidates=len(request.candidates),
            pipeline=pipeline.name,
        )

    async def rank_async(self, request: RankRequest) -> RankResult:
        """异步执行一次排序请求，支持异步 reranker。"""
        pipeline = self._pipeline
        if request.top_k <= 0 or not request.candidates:
            return RankResult(
                ranked=(),
                total_candidates=len(request.candidates),
                pipeline=pipeline.name,
            )

        # 1. 所有 scorer 打分（支持异步 scorer）
        signals = await self._collect_signals_async(
            request=request,
            pipeline=pipeline,
        )

        # 2. fusion 融合信号
        ranked = pipeline.fusion.fuse(
            candidates=request.candidates,
            signals=signals,
        )
        ranked = self._assign_rank(ranked)

        if request.candidate_limit <= 0:
            return RankResult(
                ranked=(),
                total_candidates=len(request.candidates),
                pipeline=pipeline.name,
            )

        ranked = ranked[: request.candidate_limit]

        # 3. 二次重排（可选）
        if pipeline.reranker is not None:
            ranked = await pipeline.reranker.rerank(
                query=request.query,
                ranked=ranked,
            )
            ranked = self._assign_rank(ranked)
            ranked = ranked[: request.candidate_limit]

        # 4. 多样性控制（可选）
        if pipeline.diversifier is not None:
            ranked = pipeline.diversifier.diversify(ranked=ranked)
            ranked = self._assign_rank(ranked)

        # 5. top_k 截断
        ranked = self._assign_rank(ranked[: request.top_k])

        return RankResult(
            ranked=ranked,
            total_candidates=len(request.candidates),
            pipeline=pipeline.name,
        )

    @staticmethod
    def _collect_signals(
        *,
        request: RankRequest,
        pipeline,
    ) -> tuple[ScoreSignal, ...]:
        """收集所有 scorer 产出的排序信号。"""
        signals: list[ScoreSignal] = []

        for scorer in pipeline.scorers:
            signals.extend(
                scorer.score(
                    query=request.query,
                    candidates=request.candidates,
                )
            )

        return tuple(signals)

    @staticmethod
    async def _collect_signals_async(
        *,
        request: RankRequest,
        pipeline,
    ) -> tuple[ScoreSignal, ...]:
        """异步收集所有 scorer 产出的排序信号。"""
        signals: list[ScoreSignal] = []

        for scorer in pipeline.scorers:
            score_async = getattr(scorer, "score_async", None)
            if score_async is None:
                signals.extend(
                    scorer.score(
                        query=request.query,
                        candidates=request.candidates,
                    )
                )
                continue

            signals.extend(
                await score_async(
                    query=request.query,
                    candidates=request.candidates,
                )
            )

        return tuple(signals)

    @staticmethod
    def _assign_rank(
        ranked: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:
        """重新分配连续 rank。"""
        return tuple(
            RankedCandidate(
                candidate=item.candidate,
                rank=index,
                score=item.score,
                signals=item.signals,
                reason=item.reason,
                metadata=item.metadata,
            )
            for index, item in enumerate(ranked, 1)
        )