from __future__ import annotations

from functools import lru_cache

from tokenizers import Encoding, Tokenizer

_TOKENIZER_ID = "deepseek-ai/DeepSeek-V3"
_TOKENIZER_REVISION = "e815299b0bcbac849fa540c768ef21845365c9eb"
_TRUNCATION_MARKER = "\n...\n"
_INITIAL_CHARS_PER_TOKEN = 8
_MIN_PROBE_CHARS = 1024


@lru_cache(maxsize=1)
def _tokenizer() -> Tokenizer:
    return Tokenizer.from_pretrained(
        _TOKENIZER_ID,
        revision=_TOKENIZER_REVISION,
    )


def count_canonical_tokens(text: str) -> int:
    return len(_encode(text).ids)


def bounded_canonical_token_count(text: str, token_limit: int) -> int:
    prefix, _, truncated = truncate_canonical_prefix(text, token_limit)
    if truncated:
        return token_limit
    return count_canonical_tokens(prefix)


def truncate_canonical_prefix(
    text: str,
    token_budget: int,
) -> tuple[str, int, bool]:
    if not text:
        return "", 0, False
    if token_budget <= 0:
        return "", 0, True

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
        probe_end = min(len(text), probe_end * 2)


def truncate_canonical_suffix(
    text: str,
    token_budget: int,
) -> tuple[str, int, bool]:
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
        probe_chars = min(len(text), probe_chars * 2)


def canonical_preview(text: str, token_budget: int) -> tuple[str, bool]:
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
        tail_budget -= max(overflow, 1)

    prefix, _, _ = truncate_canonical_prefix(text, token_budget)
    return prefix, True


def _encode(text: str) -> Encoding:
    return _tokenizer().encode(text, add_special_tokens=False)


def _prefix_from_encoding(
    text: str,
    encoding: Encoding,
    token_budget: int,
) -> tuple[str, int]:
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
    if token_budget <= 0:
        return "", len(text)
    if len(encoding.ids) <= token_budget:
        return text, 0

    token_index = len(encoding.ids) - token_budget
    while token_index < len(encoding.ids):
        start_offset = encoding.offsets[token_index][0]
        suffix = text[start_offset:]
        overflow = count_canonical_tokens(suffix) - token_budget
        if overflow <= 0:
            return suffix, start_offset
        token_index += overflow
    return "", len(text)

