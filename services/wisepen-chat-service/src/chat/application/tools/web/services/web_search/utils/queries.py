import re
from typing import List, Optional, Set, Tuple

from chat.application.tools.web.services.web_search.utils.notes import add_note

MAX_SEARCH_QUERY_CHARS: int = 400
_STRIP_CHARS = " ,.;!?，。；！？_|-"
_WORD_SUFFIX_RE = re.compile(r"[A-Za-z]+$")
_NON_TOKEN_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")
_CJK_RE = re.compile(r"([\u4e00-\u9fff])")
_JACCARD_THRESHOLD = 0.85


def _safe_truncate_query(query: str) -> Tuple[str, bool]:
    """安全截断搜索查询字符串，尽量保留完整单词。

    超过 MAX_SEARCH_QUERY_CHARS 时截断，并尝试移除尾部不完整的
    英文单词后缀，避免查询在单词中间被切断。

    Args:
        query: 原始查询字符串。

    Returns:
        (截断后的查询, 是否发生过截断) 的二元组。
    """
    if len(query) <= MAX_SEARCH_QUERY_CHARS:
        return query, False

    candidate = query[:MAX_SEARCH_QUERY_CHARS]
    match = _WORD_SUFFIX_RE.search(candidate)
    if match:
        suffix_len = len(match.group())
        if suffix_len < len(candidate) and suffix_len < 30:
            candidate = candidate[:-suffix_len].rstrip()

    return candidate.strip(_STRIP_CHARS), True


def normalize_queries(
    queries: List[str],
    *,
    notes: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> List[str]:
    """归一化工具侧传入的搜索查询列表。

    依次执行：空白折叠 → 安全截断 → 分词 → Jaccard 语义去重，
    确保发给搜索引擎的 query 干净、无冗余。

    Args:
        queries: 原始搜索查询字符串列表。
        notes: 可选，用于收集处理过程中的备注信息（截断、去重、超限等）。
        limit: 可选，返回的最大查询数量，超出部分丢弃。

    Returns:
        归一化处理后的查询字符串列表。
    """
    normalized: List[str] = []
    seen_token_sets: List[Set[str]] = []
    truncated_count = 0
    skipped_duplicates = 0
    limit_reached = False

    for query in queries:
        if limit is not None and len(normalized) >= limit:
            limit_reached = True
            break

        value = " ".join(query.strip().split())
        if not value:
            continue

        value, was_truncated = _safe_truncate_query(value)
        if was_truncated:
            truncated_count += 1

        tokens = _tokenize(value)
        if not tokens:
            continue

        if any(_is_similar(tokens, seen) for seen in seen_token_sets):
            skipped_duplicates += 1
            continue

        seen_token_sets.append(tokens)
        normalized.append(value)

    if truncated_count:
        add_note(notes, f"{truncated_count} queries truncated to {MAX_SEARCH_QUERY_CHARS} characters.")
    if skipped_duplicates:
        add_note(notes, f"{skipped_duplicates} semantically duplicate search queries were removed.")
    if limit_reached:
        add_note(notes, f"Search queries were limited to {limit} focused queries.")

    return normalized


def _tokenize(query: str) -> Set[str]:
    """将查询字符串分词为 token 集合，用于相似度比较。

    统一转小写，过滤非字母数字/非中文字符，将中文字符按单字拆分，
    英文按空格分词。

    Args:
        query: 查询字符串。

    Returns:
        token 集合。
    """
    text = _NON_TOKEN_RE.sub(" ", query.lower())
    text = _CJK_RE.sub(r" \1 ", text)
    return set(text.split())


def _is_similar(a: Set[str], b: Set[str]) -> bool:
    """判断两个 token 集合是否语义相似。

    使用 Jaccard 相似度（交集/并集）或子集关系判定，
    超过 _JACCARD_THRESHOLD（0.85）或互为子集时视为相似。

    Args:
        a: token 集合 A。
        b: token 集合 B。

    Returns:
        相似返回 True，否则返回 False。
    """
    overlap = len(a & b)
    return overlap / len(a | b) >= _JACCARD_THRESHOLD or a <= b or b <= a
