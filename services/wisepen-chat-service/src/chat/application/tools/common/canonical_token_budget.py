"""为模型可见内容提供稳定的 canonical token 预算计算。

这里的 token 不是 Python 字符数，也不是某个调用方临时选择的 tokenizer。
所有模型可见窗口都使用固定模型和固定 revision 计算预算，避免不同进程、
不同 tokenizer 版本对同一段文本得出不同的截断边界。

函数返回的 offset 始终是原始 Python 字符串上的切片位置；token 预算只决定
允许保留多少内容，不会把字符 offset 替换成 token offset。
"""

from __future__ import annotations

from functools import lru_cache

from tokenizers import Encoding, Tokenizer

# tokenizer 的 revision 必须固定。若只写模型名，远端模型更新后同一文本的
# token 数可能变化，进而改变窗口边界和下游看到的内容。
_TOKENIZER_ID = "deepseek-ai/DeepSeek-V3"
_TOKENIZER_REVISION = "e815299b0bcbac849fa540c768ef21845365c9eb"

# preview 中的分隔标记本身也会消耗 token 预算，不能当作零成本装饰。
_TRUNCATION_MARKER = "\n...\n"

# token 与字符不是线性关系，这个值只用于第一次探测窗口大小，不是预算换算公式。
_INITIAL_CHARS_PER_TOKEN = 8
_MIN_PROBE_CHARS = 1024


@lru_cache(maxsize=1)
def _tokenizer() -> Tokenizer:
    """加载并缓存本进程唯一的 canonical tokenizer。"""

    return Tokenizer.from_pretrained(
        _TOKENIZER_ID,
        revision=_TOKENIZER_REVISION,
    )


def count_canonical_tokens(text: str) -> int:
    """返回文本在固定 canonical tokenizer 下的 token 数。"""

    return len(_encode(text).ids)


def bounded_canonical_token_count(text: str, token_limit: int) -> int:
    """返回不超过 `token_limit` 的 token 数。

    文本超过限制时无需继续精确计算完整文本的 token 数；调用方只需要知道
    它已经触及上限，因此直接返回 `token_limit`。
    """

    prefix, _, truncated = truncate_canonical_prefix(text, token_limit)
    if truncated:
        return token_limit
    return count_canonical_tokens(prefix)


def truncate_canonical_prefix(
    text: str,
    token_budget: int,
) -> tuple[str, int, bool]:
    """保留文本前缀，并返回 `(prefix, character_end, truncated)`。

    采用逐步扩大探测区间，而不是一次编码完整长文本：大多数内容只需要
    看预算附近的一小段就能找到边界。真正超预算后，再利用 tokenizer 提供
    的 token offset 将 token 边界映射回 Python 字符 offset。
    """

    if not text:
        return "", 0, False
    if token_budget <= 0:
        return "", 0, True

    # 字符数只是探测起点。中文、代码和标点的 token 密度不同，所以探测失败
    # 时必须扩大区间，不能把 `_INITIAL_CHARS_PER_TOKEN` 当成固定换算比例。
    probe_end = min(
        len(text),
        max(_MIN_PROBE_CHARS, token_budget * _INITIAL_CHARS_PER_TOKEN),
    )
    while True:
        encoding = _encode(text[:probe_end])
        if len(encoding.ids) > token_budget:
            prefix, end_offset = _prefix_from_encoding(
                text[:probe_end],
                encoding,
                token_budget,
            )
            return prefix, end_offset, True
        if probe_end == len(text):
            return text, len(text), False
        # 当前探测区间仍未超过预算，扩大后继续判断；到达全文时才可以确认
        # 文本完整落在预算内。
        probe_end = min(len(text), probe_end * 2)


def truncate_canonical_suffix(
    text: str,
    token_budget: int,
) -> tuple[str, int, bool]:
    """保留文本后缀，并返回 `(suffix, character_start, truncated)`。

    与前缀截断相反，这里从文本尾部逐步扩大探测窗口。返回的第二个值仍然
    是原始字符串的字符 offset，便于调用方直接组合原文切片。
    """

    if not text:
        return "", 0, False
    if token_budget <= 0:
        return "", len(text), True

    probe_chars = min(
        len(text),
        max(_MIN_PROBE_CHARS, token_budget * _INITIAL_CHARS_PER_TOKEN),
    )
    while True:
        probe_start = len(text) - probe_chars
        probe = text[probe_start:]
        encoding = _encode(probe)
        if len(encoding.ids) > token_budget:
            suffix, relative_start = _suffix_from_encoding(
                probe,
                encoding,
                token_budget,
            )
            return suffix, probe_start + relative_start, True
        if probe_start == 0:
            return text, 0, False
        # 后缀窗口从尾部向前扩张；只有探测到字符串起点仍未超预算时，
        # 才能确认整段文本都可以返回。
        probe_chars = min(len(text), probe_chars * 2)


def canonical_preview(text: str, token_budget: int) -> tuple[str, bool]:
    """生成受 canonical token 预算限制的预览。

    未超预算时返回原文。超预算时优先保留头部和尾部，中间用带预算成本的
    `_TRUNCATION_MARKER` 连接；如果预算连标记都容不下，则退化为纯前缀。
    """

    _, _, truncated = truncate_canonical_prefix(text, token_budget)
    if not truncated:
        return text, False
    if token_budget <= count_canonical_tokens(_TRUNCATION_MARKER):
        prefix, _, _ = truncate_canonical_prefix(text, token_budget)
        return prefix, True

    available = token_budget - count_canonical_tokens(_TRUNCATION_MARKER)
    head_budget = available - available // 2
    tail_budget = available // 2
    head, _, _ = truncate_canonical_prefix(text, head_budget)
    while tail_budget >= 0:
        tail, _, _ = truncate_canonical_suffix(text, tail_budget)
        preview = head + _TRUNCATION_MARKER + tail
        overflow = count_canonical_tokens(preview) - token_budget
        if overflow <= 0:
            return preview, True
        # 头尾分别截断后重新拼接仍可能因为边界和标记产生额外 token，
        # 因此必须对最终 preview 再验算，而不能简单相加两侧预算。
        tail_budget -= max(overflow, 1)

    prefix, _, _ = truncate_canonical_prefix(text, token_budget)
    return prefix, True


def _encode(text: str) -> Encoding:
    """编码文本但不添加特殊 token。

    `<BOS>`、`<EOS>` 等特殊 token 不属于工具正文；加入它们会让预算计算
    多出模型控制开销，并使正文窗口边界随调用方式变化。
    """

    return _tokenizer().encode(text, add_special_tokens=False)


def _prefix_from_encoding(
    text: str,
    encoding: Encoding,
    token_budget: int,
) -> tuple[str, int]:
    """把已编码文本裁成不超过预算的前缀。"""

    if token_budget <= 0:
        return "", 0
    if len(encoding.ids) <= token_budget:
        return text, len(text)

    # 第一个被排除的 token 可能与前一个 Unicode 码点重叠。
    # 从它的起点切开，保证返回前缀不会越过预算。
    end_offset = encoding.offsets[token_budget][0]
    prefix = text[:end_offset]
    while prefix and count_canonical_tokens(prefix) > token_budget:
        end_offset -= 1
        prefix = text[:end_offset]
    return prefix, end_offset


def _suffix_from_encoding(
    text: str,
    encoding: Encoding,
    token_budget: int,
) -> tuple[str, int]:
    """把已编码文本裁成不超过预算的后缀。"""

    if token_budget <= 0:
        return "", len(text)
    if len(encoding.ids) <= token_budget:
        return text, 0

    # tokenizer offset 是相对 `text` 的字符位置。每次向后移动一个可能
    # 超预算的 token，重新计数确认边界，避免 token 合并规则造成越界。
    token_index = len(encoding.ids) - token_budget
    while token_index < len(encoding.ids):
        start_offset = encoding.offsets[token_index][0]
        suffix = text[start_offset:]
        overflow = count_canonical_tokens(suffix) - token_budget
        if overflow <= 0:
            return suffix, start_offset
        token_index += overflow
    return "", len(text)

