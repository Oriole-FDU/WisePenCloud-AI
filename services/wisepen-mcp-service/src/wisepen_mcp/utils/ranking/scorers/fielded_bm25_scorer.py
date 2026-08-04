from __future__ import annotations

from dataclasses import dataclass, field

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
class FieldedBM25ScorerConfig:
    """多字段 BM25 打分配置。"""

    field_weights: dict[str, float] = field(
        default_factory=lambda: {
            "title": 3.0,  # 标题命中通常最重要
            "heading": 2.0,  # 章节标题次之
            "summary": 1.5,  # 摘要比正文更集中
        }
    )

    min_score: float = 0.0


class FieldedBM25Scorer:
    """基于 RankCandidate.fields 的多字段 BM25 打分器。"""

    __slots__ = ("tokenizer", "config")

    def __init__(
            self,
            *,
            tokenizer: RankingTokenizer,
            config: FieldedBM25ScorerConfig | None = None,
    ) -> None:
        self.tokenizer = tokenizer
        self.config = config or FieldedBM25ScorerConfig()

    def score(
            self,
            *,
            query: RankQuery,
            candidates: tuple[RankCandidate, ...],
    ) -> tuple[ScoreSignal, ...]:
        if not candidates:
            return ()

        positions = candidate_positions(candidates)

        query_tokens = list(self.tokenizer.tokenize(query.text))
        if not query_tokens:
            return ()

        cfg = self.config
        n = len(candidates)

        signals: list[ScoreSignal] = []

        for field_name, weight in cfg.field_weights.items():
            # 每个字段单独建一个 BM25 索引，字段之间不混在一起算分。
            # 默认只算 title/heading/summary 这类结构化强字段，不默认算 body，防止正文重复。
            # 字段权重不改 BM25 原始分，而是写进 ScoreSignal.weight，
            # 后续 WeightedRrfFusion 会用 signal.weight / (k + rank) 体现字段重要性。
            corpus_tokens = [
                list(
                    self.tokenizer.tokenize(candidate.fields.get(field_name, "") or "")
                )
                for candidate in candidates
            ]
            if not any(corpus_tokens):
                continue

            retriever = bm25s.BM25()
            retriever.index(corpus_tokens, show_progress=False)
            documents, scores = retriever.retrieve(
                [query_tokens],
                k=n,
                sorted=True,
                show_progress=False,
            )

            for rank, raw_index in enumerate(documents[0], 1):
                candidate = candidates[int(raw_index)]
                if not candidate.fields.get(field_name, "").strip():
                    continue
                score = float(scores[0][rank - 1])
                if score <= cfg.min_score:
                    continue
                signals.append(
                    ScoreSignal(
                        candidate_id=candidate.candidate_id,
                        name=f"bm25:{field_name}",
                        value=score,
                        kind=ScoreSignalKind.FIELD,
                        rank=rank,
                        weight=weight,  # 字段加权点：title/heading 等字段通过 signal.weight 影响融合贡献
                        reason=f"BM25 {field_name} relevance.",
                        metadata={
                            "field": field_name,
                            "method": "lucene",
                        },
                    )
                )

        return tuple(
            sorted(
                signals,
                key=lambda signal: (
                    signal.name,
                    signal.rank if signal.rank is not None else n + 1,
                    positions[signal.candidate_id],
                ),
            )
        )
