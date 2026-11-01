"""论文标题的文本规范化工具。"""

import unicodedata


_TYPOGRAPHY = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
})


def normalize_title(title: str) -> str:
    """只统一排版差异，保留停用词、副标题、词序和数学符号。"""
    return " ".join(unicodedata.normalize("NFKC", title).translate(_TYPOGRAPHY).casefold().split())
