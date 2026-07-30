import pytest

from common.utils.ranking import (
    RankCandidate,
    RankedCandidate,
    RankQuery,
    ScoreSignal,
)
from common.utils.ranking.diversifiers import (
    MmrDiversifier,
    MmrDiversifierConfig,
)
from common.utils.ranking.fusion import WeightedRrfFusion
from common.utils.ranking.scorers import (
    FieldedBM25Scorer,
    FieldedBM25ScorerConfig,
)
from common.utils.ranking.tokenizer import RankingTokenizer


class _WhitespaceTokenizer(RankingTokenizer):
    def _tokenize_cjk(self, text: str) -> tuple[str, ...]:
        return (text,)


def test_weighted_rrf_keeps_zero_score_ranked_signal() -> None:
    ranked = WeightedRrfFusion().fuse(
        candidates=(
            RankCandidate(candidate_id="a", text="alpha"),
            RankCandidate(candidate_id="b", text="beta"),
        ),
        signals=(
            ScoreSignal(candidate_id="a", name="zero", value=0.0, rank=1, weight=0.0),
        ),
    )

    assert [item.candidate_id for item in ranked] == ["a"]
    assert ranked[0].score == 0.0


def test_weighted_rrf_rejects_duplicate_candidate_ids() -> None:
    candidates = (
        RankCandidate(candidate_id="duplicate"),
        RankCandidate(candidate_id="duplicate"),
    )

    with pytest.raises(ValueError, match="Duplicate candidate_id: duplicate"):
        WeightedRrfFusion().fuse(candidates=candidates, signals=())


def test_mmr_normalizes_equal_scores() -> None:
    ranked = tuple(
        RankedCandidate(
            candidate=RankCandidate(candidate_id=str(index), text=f"text {index}"),
            rank=index,
            score=5.0,
        )
        for index in range(1, 4)
    )
    diversifier = MmrDiversifier(
        tokenizer=_WhitespaceTokenizer(),
        config=MmrDiversifierConfig(lambda_mult=1.0),
    )

    diversified = diversifier.diversify(ranked=ranked)

    assert diversified[0].metadata["mmr_score"] == pytest.approx(1.0)


def test_fielded_bm25_scores_selected_fields() -> None:
    scorer = FieldedBM25Scorer(
        tokenizer=_WhitespaceTokenizer(),
        config=FieldedBM25ScorerConfig(
            field_weights={"section": 2.0, "anchor": 1.5},
        ),
    )
    signals = scorer.score(
        query=RankQuery(text="快速开始 Table"),
        candidates=(
            RankCandidate(
                candidate_id="a",
                fields={"section": "快速开始", "anchor": "Table 1"},
            ),
            RankCandidate(candidate_id="b", fields={"other": "快速开始 Table 1"}),
        ),
    )

    assert {(signal.candidate_id, signal.name) for signal in signals} == {
        ("a", "bm25:section"),
        ("a", "bm25:anchor"),
    }
