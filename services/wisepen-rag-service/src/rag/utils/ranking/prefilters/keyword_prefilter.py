from __future__ import annotations

from dataclasses import dataclass, field

import unicodedata

from ..core import (
    RankCandidate,
    RankQuery,
)


@dataclass(frozen=True, slots=True)
class KeywordPrefilterConfig:
    """关键词硬过滤配置。"""

    text_enabled: bool = True  # 是否检查 candidate.text
    field_names: tuple[str, ...] = field(
        default_factory=lambda: ("title",)
    )  # 参与关键词硬过滤的字段名
    require_all_keywords: bool = False  # 是否要求全部关键词命中


class KeywordPrefilter:
    """基于 query metadata 中 keywords 的硬过滤器。"""

    __slots__ = ("config",)

    def __init__(
            self,
            *,
            config: KeywordPrefilterConfig | None = None,
    ) -> None:
        self.config = config or KeywordPrefilterConfig()

    def prefilter(
            self,
            *,
            query: RankQuery,
            candidates: tuple[RankCandidate, ...],
    ) -> tuple[RankCandidate, ...]:
        if not candidates:
            return ()

        normalized_keywords = self._extract_keywords(query)
        if not normalized_keywords:
            return candidates

        return tuple(
            candidate
            for candidate in candidates
            if self._matches_required_keywords(
                candidate=candidate,
                keywords=normalized_keywords,
            )
        )

    def _extract_keywords(self, query: RankQuery) -> tuple[str, ...]:
        raw_keywords = query.metadata.get("keywords")

        # 关键词由上游明确传入，避免过滤器从自然语言 query 中自行猜测硬约束。
        if not isinstance(raw_keywords, list | tuple):
            raise ValueError(
                'KeywordPrefilter requires query.metadata["keywords"], and must be list or tuple.'
            )

        normalized_keywords: list[str] = []
        seen_keywords: set[str] = set()
        for keyword in raw_keywords:
            normalized = self._normalize(str(keyword))
            if not normalized or normalized in seen_keywords:
                continue
            seen_keywords.add(normalized)
            normalized_keywords.append(normalized)

        return tuple(normalized_keywords)

    def _matches_required_keywords(
            self,
            *,
            candidate: RankCandidate,
            keywords: tuple[str, ...],
    ) -> bool:
        matched_keywords = set[str]()

        # text 和结构字段只负责判定是否命中，不再产生与 BM25 重复的排序分数。
        searchable_texts: list[str] = []
        if self.config.text_enabled:
            searchable_texts.append(candidate.text)
        searchable_texts.extend(
            candidate.fields.get(field_name, "")
            for field_name in self.config.field_names
        )

        for raw_text in searchable_texts:
            normalized_text = self._normalize(raw_text)
            if not normalized_text:
                continue

            for keyword in keywords:
                if keyword in normalized_text:
                    matched_keywords.add(keyword)

        if self.config.require_all_keywords:
            return len(matched_keywords) == len(keywords)
        return bool(matched_keywords)

    def _normalize(self, text: str) -> str:
        """归一化匹配文本。"""
        return unicodedata.normalize("NFKC", text.strip()).casefold()
