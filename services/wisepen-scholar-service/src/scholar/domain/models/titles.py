"""完整标题的格式规范化与稳定身份候选；标题本身不是论文身份。"""

import unicodedata
from dataclasses import dataclass, field

from scholar.domain.models.identifiers import IdentifierKind, PaperIdentifier


_TYPOGRAPHY = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
})


def normalize_title(title: str) -> str:
    """只统一排版差异，保留停用词、副标题、词序和数学符号。"""
    return " ".join(unicodedata.normalize("NFKC", title).translate(_TYPOGRAPHY).casefold().split())


@dataclass
class TitleMatch:
    """一个标题的候选；每个内部列表是同一论文的身份别名，多组表示歧义。"""

    candidates: list[list[PaperIdentifier]] = field(default_factory=list)
    error: str | None = None


def merge_title_identities(candidates: list[list[PaperIdentifier]]) -> list[list[PaperIdentifier]]:
    """相同 arXiv 优先合并；DOI 相同但 arXiv 不同的论文仍保持歧义。"""
    groups: list[list[PaperIdentifier]] = []
    for candidate in candidates:
        combined = list(dict.fromkeys(candidate))
        remaining = []
        for group in groups:
            left_arxiv = {i for i in combined if i.kind == IdentifierKind.ARXIV}
            right_arxiv = {i for i in group if i.kind == IdentifierKind.ARXIV}
            if set(combined).intersection(group) and (
                not left_arxiv or not right_arxiv or left_arxiv == right_arxiv
            ):
                combined = list(dict.fromkeys([*group, *combined]))
            else:
                remaining.append(group)
        groups = [*remaining, combined]
    return groups
