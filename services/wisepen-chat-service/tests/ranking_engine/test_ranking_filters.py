from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

ranking_engine_package = types.ModuleType("chat.application.utils.ranking_engine")
ranking_engine_package.__path__ = [
    str(
        Path(__file__).resolve().parents[2]
        / "src"
        / "chat"
        / "application"
        / "utils"
        / "ranking_engine"
    )
]
sys.modules["chat.application.utils.ranking_engine"] = ranking_engine_package

from chat.application.utils.ranking_engine.engine import RankingEngine
from chat.application.utils.ranking_engine.filters import KeywordFilter, KeywordFilterConfig
from chat.application.utils.ranking_engine.fusion import WeightedRrfFusion
from chat.application.utils.ranking_engine.models import (
    RankCandidate,
    RankQuery,
    RankRequest,
    ScoreSignal,
    ScoreSignalKind,
)
from chat.application.utils.ranking_engine.pipeline import RankingPipeline


class _CandidateIdScorer:
    """测试用 scorer，暴露进入打分阶段的候选集合。"""

    name = "candidate_id_scorer"

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


def test_keyword_filter_runs_before_scorers() -> None:
    engine = RankingEngine(
        pipeline=RankingPipeline(
            name="test.keyword_filter",
            filters=(
                KeywordFilter(
                    config=KeywordFilterConfig(
                        field_names=("section",),
                        require_all_keywords=True,
                    )
                ),
            ),
            scorers=(_CandidateIdScorer(),),
            fusion=WeightedRrfFusion(),
        )
    )

    result = engine.rank(
        RankRequest(
            query=RankQuery(
                text="鉴权 token",
                metadata={"keywords": ("AppBuilder", "API Key")},
            ),
            candidates=(
                RankCandidate(
                    candidate_id="a",
                    text="AppBuilder API Key 用于接口鉴权。",
                    fields={"section": "认证"},
                ),
                RankCandidate(
                    candidate_id="b",
                    text="Bearer token 也可以用于部分接口。",
                    fields={"section": "认证"},
                ),
            ),
            top_k=10,
        )
    )

    assert [item.candidate_id for item in result.ranked] == ["a"]
    assert {signal.name for signal in result.ranked[0].signals} == {"candidate_id"}


def test_keyword_filter_keeps_input_order_without_scorers() -> None:
    engine = RankingEngine(
        pipeline=RankingPipeline(
            name="test.keyword_filter.input_order",
            filters=(KeywordFilter(),),
        )
    )

    result = engine.rank(
        RankRequest(
            query=RankQuery(
                text="",
                metadata={"keywords": ("timeout",)},
            ),
            candidates=(
                RankCandidate(candidate_id="a", text="timeout 重试策略"),
                RankCandidate(candidate_id="b", text="普通鉴权说明"),
                RankCandidate(candidate_id="c", text="connect timeout 处理"),
            ),
            top_k=10,
        )
    )

    assert [item.candidate_id for item in result.ranked] == ["a", "c"]
    assert all(not item.signals for item in result.ranked)
    assert all(item.metadata["initial_ranker"] == "input_order" for item in result.ranked)


def test_keyword_filter_requires_explicit_keywords_metadata() -> None:
    engine = RankingEngine(
        pipeline=RankingPipeline(
            name="test.keyword_filter.missing_keywords",
            filters=(KeywordFilter(),),
        )
    )

    with pytest.raises(ValueError, match='query.metadata\\["keywords"\\]'):
        engine.rank(
            RankRequest(
                query=RankQuery(text="timeout"),
                candidates=(RankCandidate(candidate_id="a", text="timeout"),),
                top_k=10,
            )
        )
