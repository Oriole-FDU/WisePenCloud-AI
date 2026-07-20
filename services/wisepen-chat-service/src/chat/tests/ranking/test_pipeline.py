from __future__ import annotations

import pytest

from chat.application.utils.ranking import (
    RankCandidate,
    RankedCandidate,
    RankingPipeline,
    RankQuery,
    RankRequest,
    ScoreSignal,
    ScoreSignalKind,
)
from chat.application.utils.ranking.fusion import WeightedRrfFusion
from chat.application.utils.ranking.prefilters import (
    KeywordPrefilter,
    KeywordPrefilterConfig,
)


class _CandidateIdScorer:
    def score(
        self,
        *,
        query: RankQuery,
        candidates: tuple[RankCandidate, ...],
    ) -> tuple[ScoreSignal, ...]:
        return tuple(
            ScoreSignal(
                candidate_id=candidate.candidate_id,
                name="candidate_id",
                value=float(len(candidates) - index),
                kind=ScoreSignalKind.RULE,
                rank=index + 1,
            )
            for index, candidate in enumerate(candidates)
        )


class _ReverseReranker:
    async def rerank(
        self,
        *,
        query: RankQuery,
        ranked: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:
        return tuple(reversed(ranked))


def test_prefilter_runs_before_scorers() -> None:
    pipeline = RankingPipeline(
        prefilters=(
            KeywordPrefilter(
                config=KeywordPrefilterConfig(
                    field_names=("section",),
                    require_all_keywords=True,
                )
            ),
        ),
        scorers=(_CandidateIdScorer(),),
        fusion=WeightedRrfFusion(),
    )

    result = pipeline.rank(
        RankRequest(
            query=RankQuery(
                text="鉴权 token",
                metadata={"keywords": ("AppBuilder", "API Key")},
            ),
            candidates=(
                RankCandidate(candidate_id="a", text="AppBuilder API Key 用于鉴权。"),
                RankCandidate(candidate_id="b", text="Bearer token 用于鉴权。"),
            ),
            top_k=10,
        )
    )

    assert [item.candidate_id for item in result.ranked] == ["a"]
    assert {signal.name for signal in result.ranked[0].signals} == {"candidate_id"}


def test_prefilter_keeps_input_order_without_scorers() -> None:
    pipeline = RankingPipeline(
        prefilters=(KeywordPrefilter(),),
    )

    result = pipeline.rank(
        RankRequest(
            query=RankQuery(text="", metadata={"keywords": ("timeout",)}),
            candidates=(
                RankCandidate(candidate_id="a", text="timeout 重试策略"),
                RankCandidate(candidate_id="b", text="普通鉴权说明"),
                RankCandidate(candidate_id="c", text="connect timeout 处理"),
            ),
            top_k=10,
        )
    )

    assert [item.candidate_id for item in result.ranked] == ["a", "c"]
    assert all(
        item.metadata["initial_ranker"] == "input_order" for item in result.ranked
    )


def test_sync_pipeline_rejects_async_reranker() -> None:
    pipeline = RankingPipeline(reranker=_ReverseReranker())

    with pytest.raises(RuntimeError, match="arank"):
        pipeline.rank(
            RankRequest(
                query=RankQuery(text="query"),
                candidates=(RankCandidate(candidate_id="a"),),
                top_k=1,
            )
        )


@pytest.mark.asyncio
async def test_arank_applies_reranker() -> None:
    pipeline = RankingPipeline(reranker=_ReverseReranker())
    result = await pipeline.arank(
        RankRequest(
            query=RankQuery(text="query"),
            candidates=(
                RankCandidate(candidate_id="a"),
                RankCandidate(candidate_id="b"),
            ),
            top_k=2,
        )
    )

    assert [item.candidate_id for item in result.ranked] == ["b", "a"]
    assert [item.rank for item in result.ranked] == [1, 2]


@pytest.mark.asyncio
async def test_arank_offloads_sync_ranking_stages(
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    offloaded: list[str] = []

    async def run_in_thread(function, /, *args, **kwargs):
        offloaded.append(function.__name__)
        return function(*args, **kwargs)

    monkeypatch.setattr(
        "chat.application.utils.ranking.pipeline.asyncio.to_thread",
        run_in_thread,
    )
    pipeline = RankingPipeline(
        scorers=(_CandidateIdScorer(),),
        fusion=WeightedRrfFusion(),
    )

    result = await pipeline.arank(
        RankRequest(
            query=RankQuery(text="query"),
            candidates=(RankCandidate(candidate_id="a"),),
            top_k=1,
        )
    )

    assert [item.candidate_id for item in result.ranked] == ["a"]
    assert offloaded == ["_rank_before_reranker"]
