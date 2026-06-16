from __future__ import annotations

from dataclasses import dataclass, field

import unicodedata

from chat.application.utils.ranking_engine.models import (
    RankCandidate,
    RankQuery,
    ScoreSignal,
    ScoreSignalKind,
)


@dataclass(frozen=True, slots=True)
class KeywordScorerConfig:
    """关键词命中打分配置。"""

    signal_name: str = "keyword:match"  # 信号名称
    text_weight: float = 1.0  # candidate.text 命中贡献
    field_weights: tuple[tuple[str, float], ...] = field(
        default_factory=lambda: (
            ("title", 3.0),
            ("heading", 2.0),
            ("summary", 1.5),
        )
    )
    case_sensitive: bool = False  # 是否大小写敏感
    normalize_unicode: bool = True  # 是否做 NFKC 归一化
    min_score: float = 0.0  # 最小保留分数
    require_all_keywords: bool = False  # 是否要求全部关键词命中


class KeywordScorer:
    """基于 query keywords 的通用关键词/精确命中打分器。"""

    __slots__ = ("config", "name")

    def __init__(
        self,
        *,
        config: KeywordScorerConfig | None = None,
    ) -> None:
        self.config = config or KeywordScorerConfig()
        self.name = "keyword_scorer"

    def score(
        self,
        *,
        query: RankQuery,
        candidates: tuple[RankCandidate, ...],
    ) -> tuple[ScoreSignal, ...]:
        if not candidates:
            return ()

        candidate_order: dict[str, int] = {}
        for index, candidate in enumerate(candidates):
            if candidate.candidate_id in candidate_order:
                raise ValueError(f"Duplicate candidate_id: {candidate.candidate_id}")
            candidate_order[candidate.candidate_id] = index

        cfg = self.config
        raw_keywords = query.metadata.get("keywords")

        # 关键词必须由上游显式传入 query.metadata["keywords"]，且必须是 list/tuple。
        # 单个字符串也要由上游包装成 ("keyword",) 或 ["keyword"]。
        if not isinstance(raw_keywords, list | tuple):
            raise ValueError('KeywordScorer requires query.metadata["keywords"], and must be list or tuple.')

        normalized_keywords: list[str] = []
        seen_keywords: set[str] = set()
        for keyword in raw_keywords:
            # 统一做大小写/Unicode 归一化，并按首次出现顺序去重。
            # 后面匹配 candidate 文本时也走同一个 normalize，保证比较口径一致。
            normalized = self._normalize(str(keyword))
            if not normalized or normalized in seen_keywords:
                continue
            seen_keywords.add(normalized)
            normalized_keywords.append(normalized)

        if not normalized_keywords:
            return ()

        keyword_count = len(normalized_keywords)
        scored: list[tuple[int, float, RankCandidate, tuple[str, ...], int]] = []

        for candidate in candidates:
            keyword_scores: dict[str, float] = {}

            # text 显式单独配置；一个关键词在 text 内命中一次即可，不按出现次数累加。
            # 公式：keyword_scores[keyword] += text_weight
            normalized_text = self._normalize(candidate.text)
            if normalized_text:
                for keyword in normalized_keywords:
                    if keyword in normalized_text:
                        keyword_scores[keyword] = keyword_scores.get(keyword, 0.0) + cfg.text_weight

            # field_weights 只定义字段二元组：(字段名, 命中贡献)。
            # 字段不存在或为空就跳过，不引入默认字段权重。
            # 公式：keyword_scores[keyword] += field_weight
            for field_name, field_weight in cfg.field_weights:
                normalized_field = self._normalize(candidate.fields.get(field_name, ""))
                if not normalized_field:
                    continue
                for keyword in normalized_keywords:
                    if keyword in normalized_field:
                        keyword_scores[keyword] = keyword_scores.get(keyword, 0.0) + field_weight

            matched_keywords = tuple(keyword_scores.keys())
            matched_count = len(matched_keywords)
            if cfg.require_all_keywords and matched_count < keyword_count:
                continue

            # 总分就是 text 命中贡献和字段命中贡献之和。
            # 公式：score = sum(keyword_scores.values())
            # 等价于：所有命中的 keyword，在 text 和各字段中的命中权重累计。
            score = sum(keyword_scores.values())
            if score <= cfg.min_score:
                continue

            scored.append(
                (
                    candidate_order[candidate.candidate_id],
                    score,
                    candidate,
                    matched_keywords,
                    matched_count,
                )
            )

        scored.sort(key=lambda item: (-item[1], item[0]))

        return tuple(
            ScoreSignal(
                candidate_id=candidate.candidate_id,
                name=cfg.signal_name,
                value=float(score),
                kind=ScoreSignalKind.RULE,
                rank=rank,
                weight=1.0,
                reason="Keyword match.",
                metadata={
                    "scorer": self.name,
                    "matched_keywords": matched_keywords,
                    "matched_count": matched_count,
                    "total_keywords": keyword_count,
                },
            )
            for rank, (_, score, candidate, matched_keywords, matched_count) in enumerate(scored, 1)
        )

    def _normalize(self, text: str) -> str:
        """归一化匹配文本。"""
        value = unicodedata.normalize("NFKC", text.strip()) if self.config.normalize_unicode else text.strip()
        return value if self.config.case_sensitive else value.casefold()
