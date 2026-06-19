from __future__ import annotations

from dataclasses import dataclass, field

import bm25s

from chat.application.utils.ranking_engine.models import (
    RankCandidate,
    RankQuery,
    ScoreSignal,
    ScoreSignalKind,
)
from ..text import RankingTokenizer


@dataclass(frozen=True, slots=True)
class FieldedBM25ScorerConfig:
    """多字段 BM25 打分配置。"""

    field_weights: dict[str, float] = field(
        default_factory=lambda: {
            "title": 3.0,    # 标题命中通常最重要
            "heading": 2.0,  # 章节标题次之
            "summary": 1.5,  # 摘要比正文更集中
        }
    )  

    signal_prefix: str = "bm25"
    min_score: float = 0.0
    method: str = "lucene"
    k1: float = 1.5
    b: float = 0.75
    retrieve_k: int | None = None  # None 表示取全部候选


class FieldedBM25Scorer:
    """基于 RankCandidate.fields 的多字段 BM25 打分器。"""

    __slots__ = ("tokenizer", "config", "name")

    def __init__(
        self,
        *,
        tokenizer: RankingTokenizer,
        config: FieldedBM25ScorerConfig | None = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.config = config or FieldedBM25ScorerConfig()
        self.name = "fielded_bm25_scorer"

    def score(
        self,
        *,
        query: RankQuery,
        candidates: tuple[RankCandidate, ...],
    ) -> tuple[ScoreSignal, ...]:
        if not candidates:
            return ()

        candidate_order: dict[str, int] = {}
        for i, candidate in enumerate(candidates):
            if candidate.candidate_id in candidate_order:
                raise ValueError(f"Duplicate candidate_id: {candidate.candidate_id}")
            candidate_order[candidate.candidate_id] = i

        query_tokens = [
            tokens
            for tokens in (list(self.tokenizer.tokenize(q)) for q in query.all_queries)
            if tokens
        ]
        if not query_tokens:
            return ()

        cfg = self.config
        n = len(candidates)
        retrieve_k = n if cfg.retrieve_k is None else min(max(cfg.retrieve_k, 0), n)
        if retrieve_k <= 0:
            return ()

        signals: list[ScoreSignal] = []

        for field_name, weight in cfg.field_weights.items():
            # 每个字段单独建一个 BM25 索引，字段之间不混在一起算分。
            # 默认只算 title/heading/summary 这类结构化强字段，不默认算 body，防止正文重复。
            # 字段权重不改 BM25 原始分，而是写进 ScoreSignal.weight，
            # 后续 WeightedRrfFusion 会用 signal.weight / (k + rank) 体现字段重要性。
            corpus_tokens = [
                list(self.tokenizer.tokenize(candidate.fields.get(field_name, "") or ""))
                for candidate in candidates
            ]
            if not any(corpus_tokens):
                continue

            retriever = bm25s.BM25(method=cfg.method, k1=cfg.k1, b=cfg.b)
            retriever.index(corpus_tokens, show_progress=False)
            documents, scores = retriever.retrieve(
                query_tokens,
                k=retrieve_k,
                sorted=True,
                show_progress=False,
            )

            best: dict[str, tuple[float, int]] = {}
            for qi in range(len(query_tokens)):
                for ri, raw_ci in enumerate(documents[qi], 1):
                    candidate = candidates[int(raw_ci)]
                    if not (candidate.fields.get(field_name, "") or "").strip():
                        continue
                    score = float(scores[qi][ri - 1])
                    current = best.get(candidate.candidate_id)
                    if (
                        current is None
                        or score > current[0]
                        or (score == current[0] and ri < current[1])
                    ):
                        # 多 query 命中同一候选同一字段时，只保留该字段下最高 BM25 分和对应 rank。
                        best[candidate.candidate_id] = (score, ri)

            signals.extend(
                ScoreSignal(
                    candidate_id=candidate_id,
                    name=f"{cfg.signal_prefix}:{field_name}",
                    value=score,
                    kind=ScoreSignalKind.FIELD,
                    rank=rank,
                    weight=weight,  # 字段加权点：title/heading 等字段通过 signal.weight 影响融合贡献
                    reason=f"BM25 {field_name} relevance.",
                    metadata={
                        "scorer": self.name,
                        "field": field_name,
                        "method": cfg.method,
                        "query_count": len(query_tokens),
                    },
                )
                for candidate_id, (score, rank) in best.items()
                if score > cfg.min_score
            )

        return tuple(
            sorted(
                signals,
                key=lambda signal: (
                    signal.name,
                    signal.rank if signal.rank is not None else n + 1,
                    candidate_order[signal.candidate_id],
                ),
            )
        )

