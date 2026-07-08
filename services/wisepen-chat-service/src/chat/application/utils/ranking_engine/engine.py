from __future__ import annotations

from .models import RankCandidate, RankedCandidate, RankRequest, RankResult, ScoreSignal
from .pipeline import RankingPipeline


class RankingEngine:
    """排序引擎，负责按 pipeline 编排 filter、scorer、fusion、reranker 和 diversifier。"""

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

        candidates = self._apply_filters(request=request, pipeline=pipeline)
        if not candidates:
            return RankResult(
                ranked=(),
                total_candidates=len(request.candidates),
                pipeline=pipeline.name,
            )

        ranked = self._build_initial_ranked(
            request=request,
            pipeline=pipeline,
            candidates=candidates,
        )

        if request.candidate_limit <= 0:
            return RankResult(
                ranked=(),
                total_candidates=len(request.candidates),
                pipeline=pipeline.name,
            )

        # candidate_limit 截断，减少后续阶段计算量
        ranked = ranked[: request.candidate_limit]

        # 多样性控制
        for diversifier in pipeline.diversifiers:
            ranked = diversifier.diversify(ranked=ranked)
            ranked = self._assign_rank(ranked)

        # top_k 截断，最终输出
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

        candidates = self._apply_filters(request=request, pipeline=pipeline)
        if not candidates:
            return RankResult(
                ranked=(),
                total_candidates=len(request.candidates),
                pipeline=pipeline.name,
            )

        ranked = self._build_initial_ranked(
            request=request,
            pipeline=pipeline,
            candidates=candidates,
        )

        if request.candidate_limit <= 0:
            return RankResult(
                ranked=(),
                total_candidates=len(request.candidates),
                pipeline=pipeline.name,
            )

        ranked = ranked[: request.candidate_limit]

        # 二次重排（可选）
        if pipeline.reranker is not None:
            ranked = await pipeline.reranker.rerank(
                query=request.query,
                ranked=ranked,
            )
            ranked = self._assign_rank(ranked)
            ranked = ranked[: request.candidate_limit]

        # 多样性控制（可选）
        for diversifier in pipeline.diversifiers:
            ranked = diversifier.diversify(ranked=ranked)
            ranked = self._assign_rank(ranked)

        # top_k 截断
        ranked = self._assign_rank(ranked[: request.top_k])

        return RankResult(
            ranked=ranked,
            total_candidates=len(request.candidates),
            pipeline=pipeline.name,
        )

    @staticmethod
    def _build_initial_ranked(
            *,
            request: RankRequest,
            pipeline: RankingPipeline,
            candidates: tuple[RankCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:
        """构造进入 reranker/diversifier 前的初始排序。"""
        if request.signals:
            if pipeline.fusion is None:
                raise RuntimeError("RankRequest has external signals but pipeline has no fusion.")

            return RankingEngine._assign_rank(
                pipeline.fusion.fuse(
                    candidates=candidates,
                    signals=request.signals,
                )
            )

        if not pipeline.scorers:
            return RankingEngine._assign_rank(
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

        if pipeline.fusion is None:
            raise RuntimeError("Pipeline has scorers but no fusion.")

        signals = RankingEngine._collect_signals(
            request=request,
            pipeline=pipeline,
            candidates=candidates,
        )
        ranked = pipeline.fusion.fuse(
            candidates=candidates,
            signals=signals,
        )
        return RankingEngine._assign_rank(ranked)

    @staticmethod
    def _apply_filters(
            *,
            request: RankRequest,
            pipeline: RankingPipeline,
    ) -> tuple[RankCandidate, ...]:
        """按 pipeline 声明顺序应用硬过滤器。"""
        candidates = request.candidates
        for filter_ in pipeline.filters:
            candidates = filter_.filter(
                query=request.query,
                candidates=candidates,
            )
            if not candidates:
                return ()
        return candidates

    @staticmethod
    def _collect_signals(
            *,
            request: RankRequest,
            pipeline: RankingPipeline,
            candidates: tuple[RankCandidate, ...],
    ) -> tuple[ScoreSignal, ...]:
        """收集所有 scorer 产出的排序信号。"""
        signals: list[ScoreSignal] = []

        for scorer in pipeline.scorers:
            signals.extend(
                scorer.score(
                    query=request.query,
                    candidates=candidates,
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
