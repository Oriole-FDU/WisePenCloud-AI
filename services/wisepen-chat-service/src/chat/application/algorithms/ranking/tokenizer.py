import re
from typing import List

import jieba

# BM25 检索专用停用词表：英文取 Lucene Standard 核心词，中文取常见结构词。
BM25_STOPWORDS = {
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "for", "with", "by",
    "is", "are", "was", "were", "be", "as", "at", "from", "this", "that", "it",

    "的", "地", "得", "了", "着", "过", "是", "在", "和", "与", "及", "或",
    "一个", "一些", "这个", "那个", "之", "由", "于", "及至", "并且", "由于",
    "而且", "因此", "对于", "关于", "基于", "通过", "随着", "作为", "把",
    "被", "让", "给", "往", "到", "自", "等", "及其他", "与其", "从而",
}

# 中文连续片段交给 jieba；拉丁/数字片段只识别通用连接符，不引入业务或 CS 专项语义。
RE_CJK = re.compile(r"[\u4e00-\u9fff]+")
RE_ALNUM_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")
RE_TOKEN_DELIMITER = re.compile(r"[._-]+")
RE_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)


def tokenize_for_bm25(text: str) -> List[str]:
    """BM25 通用中英混合分词器。

    英文和数字基于原文抽取后在 token 层 casefold，避免提前 lower 破坏 CamelCase 边界。
    对带点、下划线、连字符的普通复合词，保留整体 token 并补充分片 token。
    """
    raw_text = (text or "").strip()
    tokens: List[str] = []

    for match in RE_ALNUM_TOKEN.finditer(raw_text):
        for token in _expand_alnum_token(match.group(0)):
            _append_token(tokens, token)

    chinese_text = " ".join(RE_CJK.findall(raw_text))
    if chinese_text:
        for token in jieba.cut_for_search(chinese_text):
            _append_token(tokens, token)

    return tokens


def _expand_alnum_token(raw_token: str) -> List[str]:
    """展开通用字母数字 token：整体、连接符分片、CamelCase 分片。"""
    expanded: List[str] = [raw_token]
    parts = [part for part in RE_TOKEN_DELIMITER.split(raw_token) if part]

    for part in parts:
        expanded.append(part)
        expanded.extend(RE_CAMEL_BOUNDARY.split(part))

    return expanded


def _append_token(tokens: List[str], token: str) -> None:
    """归一化写入 token，保持首次出现顺序并过滤停用词。"""
    normalized = token.strip("._-").casefold()
    if not normalized or normalized in BM25_STOPWORDS:
        return
    if normalized not in tokens:
        tokens.append(normalized)
