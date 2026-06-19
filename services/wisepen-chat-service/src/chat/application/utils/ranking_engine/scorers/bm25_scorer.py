from __future__ import annotations

from dataclasses import dataclass

import bm25s

from chat.application.utils.ranking_engine.models import (
    RankCandidate,
    RankQuery,
    ScoreSignal,
    ScoreSignalKind,
)
from ..text import RankingTokenizer


@dataclass(frozen=True, slots=True)
class BM25ScorerConfig:
    """BM25 文本打分配置。"""

    signal_name: str = "bm25:text"
    weight: float = 1.0
    min_score: float = 0.0
    method: str = "lucene"
    k1: float = 1.5
    b: float = 0.75
    retrieve_k: int | None = None  # None 表示取全部候选


class BM25Scorer:
    """基于 candidate.text 的 BM25 词法相关性打分器。"""

    __slots__ = ("tokenizer", "config", "name")

    def __init__(
        self,
        *,
        tokenizer: RankingTokenizer,
        config: BM25ScorerConfig | None = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.config = config or BM25ScorerConfig()
        self.name = "bm25_scorer"

    def score(
        self,
        *,
        query: RankQuery,
        candidates: tuple[RankCandidate, ...],
    ) -> tuple[ScoreSignal, ...]:
        if not candidates:
            return ()

        # 构建原始顺序，检测重复 candidate_id
        candidate_order: dict[str, int] = {}
        for i, c in enumerate(candidates):
            if c.candidate_id in candidate_order:
                raise ValueError(f"Duplicate candidate_id: {c.candidate_id}")
            candidate_order[c.candidate_id] = i

        corpus_tokens = [list(self.tokenizer.tokenize(c.text)) for c in candidates]
        if not any(corpus_tokens):
            return ()

        query_tokens = [
            toks
            for toks in (list(self.tokenizer.tokenize(q)) for q in query.all_queries)
            if toks
        ]
        if not query_tokens:
            return ()

        cfg = self.config
        n = len(candidates)
        retrieve_k = n if cfg.retrieve_k is None else min(max(cfg.retrieve_k, 0), n)
        if retrieve_k <= 0:
            return ()

        retriever = bm25s.BM25(method=cfg.method, k1=cfg.k1, b=cfg.b)
        retriever.index(corpus_tokens, show_progress=False)
        documents, scores = retriever.retrieve(
            query_tokens, k=retrieve_k, sorted=True, show_progress=False
        )

        # 多 query 聚合：按 candidate 保留最高分及其 rank
        best: dict[str, tuple[float, int]] = {}
        for qi in range(len(query_tokens)):
            for ri, raw_ci in enumerate(documents[qi], 1):
                cid = candidates[int(raw_ci)].candidate_id
                score = float(scores[qi][ri - 1])
                cur = best.get(cid)
                if cur is None or score > cur[0] or (score == cur[0] and ri < cur[1]):
                    best[cid] = (score, ri)

        signals = [
            ScoreSignal(
                candidate_id=cid,
                name=cfg.signal_name,
                value=score,
                kind=ScoreSignalKind.LEXICAL,
                rank=rank,
                weight=cfg.weight,
                reason="BM25 text relevance.",
                metadata={
                    "scorer": self.name,
                    "method": cfg.method,
                    "query_count": len(query_tokens),
                },
            )
            for cid, (score, rank) in best.items()
            if score > cfg.min_score
        ]

        return tuple(
            sorted(
                signals,
                key=lambda s: (
                    s.rank if s.rank is not None else n + 1,
                    candidate_order[s.candidate_id],
                ),
            )
        )
