from __future__ import annotations

from dataclasses import dataclass

import bm25s

from .._utils import candidate_positions
from ..core import (
    RankCandidate,
    RankQuery,
    ScoreSignal,
    ScoreSignalKind,
)
from ..tokenizer import RankingTokenizer


@dataclass(frozen=True, slots=True)
class BM25ScorerConfig:
    """BM25 文本打分配置。"""

    weight: float = 1.0
    min_score: float = 0.0


class BM25Scorer:
    """基于 candidate.text 的 BM25 词法相关性打分器。"""

    __slots__ = ("tokenizer", "config")

    def __init__(
            self,
            *,
            tokenizer: RankingTokenizer,
            config: BM25ScorerConfig | None = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.config = config or BM25ScorerConfig()

    def score(
            self,
            *,
            query: RankQuery,
            candidates: tuple[RankCandidate, ...],
    ) -> tuple[ScoreSignal, ...]:
        if not candidates:
            return ()

        positions = candidate_positions(candidates)

        corpus_tokens = [
            list(self.tokenizer.tokenize(candidate.text)) for candidate in candidates
        ]
        if not any(corpus_tokens):
            return ()

        query_tokens = list(self.tokenizer.tokenize(query.text))
        if not query_tokens:
            return ()

        cfg = self.config
        n = len(candidates)

        retriever = bm25s.BM25()
        retriever.index(corpus_tokens, show_progress=False)
        documents, scores = retriever.retrieve(
            [query_tokens],
            k=n,
            sorted=True,
            show_progress=False,
        )

        signals: list[ScoreSignal] = []
        for rank, raw_index in enumerate(documents[0], 1):
            score = float(scores[0][rank - 1])
            if score <= cfg.min_score:
                continue
            signals.append(
                ScoreSignal(
                    candidate_id=candidates[int(raw_index)].candidate_id,
                    name="bm25:text",
                    value=score,
                    kind=ScoreSignalKind.LEXICAL,
                    rank=rank,
                    weight=cfg.weight,
                    reason="BM25 text relevance.",
                    metadata={"method": "lucene"},
                )
            )

        return tuple(
            sorted(
                signals,
                key=lambda signal: (
                    signal.rank if signal.rank is not None else n + 1,
                    positions[signal.candidate_id],
                ),
            )
        )
