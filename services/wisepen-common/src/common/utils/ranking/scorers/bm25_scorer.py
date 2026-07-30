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

        query_tokens = [
            tokens
            for tokens in (
                list(self.tokenizer.tokenize(text)) for text in query.all_queries
            )
            if tokens
        ]
        if not query_tokens:
            return ()

        cfg = self.config
        n = len(candidates)

        retriever = bm25s.BM25()
        retriever.index(corpus_tokens, show_progress=False)
        documents, scores = retriever.retrieve(
            query_tokens,
            k=n,
            sorted=True,
            show_progress=False,
        )

        # 多 query 聚合：按 candidate 保留最高分及其 rank
        best: dict[str, tuple[float, int]] = {}
        for query_index in range(len(query_tokens)):
            for rank, raw_index in enumerate(documents[query_index], 1):
                candidate_id = candidates[int(raw_index)].candidate_id
                score = float(scores[query_index][rank - 1])
                current = best.get(candidate_id)
                if (
                        current is None
                        or score > current[0]
                        or (score == current[0] and rank < current[1])
                ):
                    best[candidate_id] = (score, rank)

        signals = [
            ScoreSignal(
                candidate_id=candidate_id,
                name="bm25:text",
                value=score,
                kind=ScoreSignalKind.LEXICAL,
                rank=rank,
                weight=cfg.weight,
                reason="BM25 text relevance.",
                metadata={
                    "method": "lucene",
                    "query_count": len(query_tokens),
                },
            )
            for candidate_id, (score, rank) in best.items()
            if score > cfg.min_score
        ]

        return tuple(
            sorted(
                signals,
                key=lambda signal: (
                    signal.rank if signal.rank is not None else n + 1,
                    positions[signal.candidate_id],
                ),
            )
        )
