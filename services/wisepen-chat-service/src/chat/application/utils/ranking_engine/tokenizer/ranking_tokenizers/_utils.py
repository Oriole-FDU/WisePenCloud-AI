from __future__ import annotations

import re

# 混合 Token 正则表达式（优先级：compound > alnum > cjk）
# compound：带分隔符的复合词（如 GPT-4、api_key）
# alnum：纯英数 token
# cjk：连续中文字符
_TOKEN_PATTERN = re.compile(
    r"(?P<compound>[A-Za-z0-9]+(?:[._\-/][A-Za-z0-9]+)+)"
    r"|(?P<alnum>[A-Za-z0-9]+)"
    r"|(?P<cjk>[\u4e00-\u9fff]+)"
)
# 复合词内部分隔符（点、下划线、横线、斜杠）
_COMMON_SEPARATOR_PATTERN = re.compile(r"[._\-/]+")


def match_token_pattern(text: str):
    """对外暴露 _TOKEN_PATTERN 的 finditer 结果。"""
    return _TOKEN_PATTERN.finditer(text)


def has_common_separator(value: str) -> bool:
    """判断 token 是否包含通用分隔符。"""
    return bool(_COMMON_SEPARATOR_PATTERN.search(value))


def split_common_compound(token: str) -> tuple[str, ...]:
    """按通用分隔符拆分复合 token，丢弃空片段。
    例如 "deep-learning" → ("deep", "learning")。
    """
    return tuple(part for part in _COMMON_SEPARATOR_PATTERN.split(token) if part)


def make_cjk_bigrams(text: str) -> tuple[str, ...]:
    """为连续中文片段生成 bigram，覆盖分词器未命中的边界。
    对于未登录词或单字切分场景，bigram 是不依赖词典的有效兜底。
    """
    chars = [char for char in text if "\u4e00" <= char <= "\u9fff"]
    if len(chars) < 2:
        return ()
    return tuple(chars[index] + chars[index + 1] for index in range(len(chars) - 1))
