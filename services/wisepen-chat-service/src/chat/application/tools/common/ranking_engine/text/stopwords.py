from __future__ import annotations


"""Ranking Engine 词法排序用轻量停用词表。"""


DEFAULT_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "by",
        "as",
        "at",
        "from",
        "的",
        "了",
        "和",
        "与",
        "及",
        "或",
        "是",
        "在",
        "对",
        "中",
        "为",
        "以",
        "于",
        "也",
        "而",
        "及其",
    }
)
