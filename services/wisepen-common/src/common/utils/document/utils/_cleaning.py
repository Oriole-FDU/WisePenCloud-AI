from __future__ import annotations

import re
from collections.abc import Sequence
from difflib import SequenceMatcher

_MAX_SECTION_DEPTH = 5
_MAX_SECTION_TITLE_CHARS = 50
_MAX_SECTION_PATH_CHARS = 120
_DUPLICATE_SIMILARITY = 0.85

# TODO: 持续收集论文解析样本，评估作者名误判、编号骨架和更宽泛重复标题规则，
# 再决定是否引入更强的上下文或模型辅助判定。

_METADATA_NOISE_PATTERN = re.compile(
    r"(?:arxiv\s*:|doi\s*:|issn\b|https?://|copyright\b|"
    r"permission\b|provided\s+proper\s+attribution)",
    re.IGNORECASE,
)
_NAVIGATION_NOISE_PATTERN = re.compile(
    r"^(?:home|index|menu|navigation|breadcrumb|sidebar|footer|header|"
    r"prev(?:ious)?|next|page\s*[-*]?\s*\d+|首页|导航|目录|返回|"
    r"上一页|下一页)$",
    re.IGNORECASE,
)
_INVALID_TITLE_PATTERN = re.compile(r"[{}|<>]")
_SENTENCE_FRAGMENT_PATTERN = re.compile(
    r"(?:^(?:where|let|assuming)\b|\b(?:instead\s+of|rather\s+than)\b)",
    re.IGNORECASE,
)
_FORMULA_PATTERN = re.compile(
    r"(?:=|\\(?:sum|prod|frac|alpha|beta|gamma|theta|lambda|cdot)\b|"
    r"\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9]+)",
)


def clean_section_title(raw_title: str) -> str | None:
    """清洗一个可能来自网页或论文解析结果的标题。"""

    title = " ".join(raw_title.split())
    if not title or len(title) > _MAX_SECTION_TITLE_CHARS:
        return None
    if (
        _METADATA_NOISE_PATTERN.search(title)
        or _NAVIGATION_NOISE_PATTERN.fullmatch(title)
        or _INVALID_TITLE_PATTERN.search(title)
    ):
        return None
    if _SENTENCE_FRAGMENT_PATTERN.search(title):
        return None
    if _FORMULA_PATTERN.search(title) and (
        "=" in title or "\\" in title or "_" in title
    ):
        return None
    return title


def clean_section_path(
    raw_path: Sequence[str] | None,
) -> tuple[str, ...] | None:
    """返回可用于结构路径的清洗后标题元组。"""

    if not raw_path or len(raw_path) > _MAX_SECTION_DEPTH:
        return None

    cleaned = tuple(
        title
        for raw_title in raw_path
        if (title := clean_section_title(raw_title)) is not None
    )
    if not cleaned:
        return None
    if len(" > ".join(cleaned)) > _MAX_SECTION_PATH_CHARS:
        return None
    return cleaned


def is_repeated_sibling_title(
    *,
    current_title: str,
    previous_title: str,
) -> bool:
    """判断相邻同层标题是否是 OCR 或图表文本造成的重复标题。"""

    if not current_title or not previous_title:
        return False
    if current_title == previous_title:
        return True
    return SequenceMatcher(
        None,
        current_title.casefold(),
        previous_title.casefold(),
    ).ratio() >= _DUPLICATE_SIMILARITY
