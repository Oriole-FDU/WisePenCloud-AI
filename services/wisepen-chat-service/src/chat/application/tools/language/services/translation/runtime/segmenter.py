from __future__ import annotations

import re
from typing import List

import sentencex
from transformers import PreTrainedTokenizerBase

# 弱标点分隔符：逗号、分号、冒号、空白等，用于对超长句子做第一步细分
_WEAK_SPLIT_PATTERN = re.compile(r"(?<=[，,、；;：:\s])")


def split_text_for_translation(
    text: str,
    source_lang: str,
    tokenizer: PreTrainedTokenizerBase,
    max_source_tokens: int,
) -> List[str]:
    """按句子边界和 token budget 将长文本切分为翻译片段。

    策略（三级降级切分）：
      1. 按 sentencex 句子边界切分主文本
      2. 超长句子先尝试按弱标点拆
      3. 仍超长的单元退化为字符级二分切分

    每级采用相同的贪心 buffer 算法：尽量合并相邻片段，
    直到超出 token budget 才 flush 为新 chunk。

    Args:
        text: 待翻译的源文本。
        source_lang: 源语言代码，用于 sentencex 句子边界检测。
        tokenizer: Marian 系列的 tokenizer，用于计算 token 数。
        max_source_tokens: 每个片段允许的最大 token 数。

    Returns:
        不超 token budget 的文本片段列表。
    """
    # --- 1. 按句子边界切分 ---
    sentences = list(sentencex.segment(source_lang, text))
    chunks: List[str] = []
    buffer = ""  # 贪心 buffer：暂存待合并的片段

    for sentence in sentences:
        if not sentence:
            continue

        sentence_tokens = _count_source_tokens(tokenizer, sentence)

        # --- 情况 A：单句超出 budget → 先 flush buffer，再对句子做降级切分 ---
        if sentence_tokens > max_source_tokens:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(
                _split_oversized_translation_unit(
                    text=sentence,
                    tokenizer=tokenizer,
                    max_source_tokens=max_source_tokens,
                )
            )
            continue

        # --- 情况 B：尝试将当前句子合并到 buffer ---
        candidate = buffer + sentence if buffer else sentence
        if _count_source_tokens(tokenizer, candidate) <= max_source_tokens:
            # 合并后未超 budget → 保留在 buffer
            buffer = candidate
        else:
            # 合并后超 budget → flush buffer，当前句子另起一段
            if buffer:
                chunks.append(buffer)
            buffer = sentence

    # 收尾：flush 剩余 buffer
    if buffer:
        chunks.append(buffer)

    return chunks


def _split_oversized_translation_unit(
    *,
    text: str,
    tokenizer: PreTrainedTokenizerBase,
    max_source_tokens: int,
) -> List[str]:
    """对超长句子按弱标点拆分，若单个单元仍超长则退化为二分切分。

    这是第二级降级策略：先用逗号、分号等弱标点切分，
    对仍超长的单元再调用 _split_by_token_budget 做字符级二分。

    Args:
        text: 超长句子文本。
        tokenizer: 用于计算 token 数的 tokenizer。
        max_source_tokens: 每个片段的最大 token 数。

    Returns:
        拆分后的文本片段列表。
    """
    # --- 2. 按弱标点（逗号、分号等）切分 ---
    weak_units = [unit for unit in _WEAK_SPLIT_PATTERN.split(text) if unit]
    chunks: List[str] = []
    buffer = ""

    for unit in weak_units:
        unit_tokens = _count_source_tokens(tokenizer, unit)

        # 单个弱标点单元仍超 budget → 退化为二分切分
        if unit_tokens > max_source_tokens:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(
                _split_by_token_budget(
                    text=unit,
                    tokenizer=tokenizer,
                    max_source_tokens=max_source_tokens,
                )
            )
            continue

        # 贪心合并：尽量把弱标点单元拼在一起
        candidate = buffer + unit if buffer else unit
        if _count_source_tokens(tokenizer, candidate) <= max_source_tokens:
            buffer = candidate
        else:
            if buffer:
                chunks.append(buffer)
            buffer = unit

    if buffer:
        chunks.append(buffer)

    # 如果弱标点拆分也未能产生任何片段，则退化到二分
    if chunks:
        return chunks

    # --- 3. 退化为字符级二分切分（最坏情况） ---
    return _split_by_token_budget(
        text=text,
        tokenizer=tokenizer,
        max_source_tokens=max_source_tokens,
    )


def _split_by_token_budget(
    *,
    text: str,
    tokenizer: PreTrainedTokenizerBase,
    max_source_tokens: int,
) -> List[str]:
    """用二分查找将文本切分为不超 token budget 的片段。

    这是第三级（最终）降级策略：在无法按语义单位拆分时，
    直接在字符级别用二分搜索找到尽可能长且不超 budget 的窗口。

    算法：对每个窗口，在 [start+1, len(text)] 范围内二分搜索
    满足 token_count <= max_source_tokens 的最大右边界。

    Args:
        text: 待切分的文本。
        tokenizer: 用于计算 token 数的 tokenizer。
        max_source_tokens: 每个片段的最大 token 数。

    Returns:
        切分后的文本片段列表。
    """
    chunks: List[str] = []
    start = 0

    while start < len(text):
        left = start + 1
        right = len(text)
        best = left

        # 二分查找：找到从 start 开始、不超过 budget 的最长字符串
        while left <= right:
            mid = (left + right) // 2
            if _count_source_tokens(tokenizer, text[start:mid]) <= max_source_tokens:
                best = mid
                left = mid + 1  # 尝试更长的窗口
            else:
                right = mid - 1  # 窗口太长，缩小

        chunks.append(text[start:best])
        start = best

    return chunks


def _count_source_tokens(tokenizer: PreTrainedTokenizerBase, text: str) -> int:
    """计算 Marian tokenizer 编码后源文本的 token 数（不含特殊 token）。"""
    return len(tokenizer.encode(text, add_special_tokens=False))
