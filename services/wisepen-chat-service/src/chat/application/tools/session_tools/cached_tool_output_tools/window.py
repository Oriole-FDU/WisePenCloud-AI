from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from common.utils.document import SourceSpan

from chat.application.tools.core.output_cache.cache_store import (
    StoredToolContent as StoredCachedToolOutput,
)


@dataclass(slots=True)
class CachedToolOutputWindow:
    """模型可见的正文窗口；只负责正文和按字符 offset 续读。"""

    text: str
    # offset 使用原文 Python 字符半开区间，供 range 工具从 end_offset 续读。
    start_offset: int
    end_offset: int
    truncated: bool = False


class CachedToolOutputWindowBuilder:
    """按统一字符预算从缓存正文构造可续读窗口。"""

    def __init__(self, *, char_budget: int) -> None:
        if char_budget < 1:
            raise ValueError("char_budget must be greater than 0")
        self._char_budget = char_budget

    def build_range_window(
        self,
        stored: StoredCachedToolOutput,
        *,
        start: int | None,
        end: int | None,
        char_budget: int | None = None,
    ) -> CachedToolOutputWindow:
        # range 工具直接按原文偏移读取；负数偏移按 Python slice 语义从末尾回退。
        text_length = len(stored.text)
        normalized_start = _normalize_offset(start, text_length, default=0)
        requested_end = _normalize_offset(end, text_length, default=text_length)
        if requested_end <= normalized_start:
            # 空区间返回空窗口。
            normalized_end = normalized_start
            truncated = False
        else:
            # 窗口预算只限制本次返回长度，不改变调用方请求的原始 end 语义。
            budget = self._resolve_budget(char_budget)
            requested_length = requested_end - normalized_start
            included_chars = min(requested_length, budget)
            truncated = requested_length > budget
            normalized_end = normalized_start + included_chars
        return CachedToolOutputWindow(
            text=stored.text[normalized_start:normalized_end],
            start_offset=normalized_start,
            end_offset=normalized_end,
            truncated=truncated,
        )

    def build_spans_window(
        self,
        stored: StoredCachedToolOutput,
        *,
        source_spans: Sequence[SourceSpan],
        char_budget: int | None = None,
    ) -> CachedToolOutputWindow:
        """把一个对象的多个原文片段聚合成单个窗口。"""

        budget = self._resolve_budget(char_budget)
        fragments: list[str] = []
        included_ranges: list[tuple[int, int]] = []
        truncated = False
        output_length = 0

        for span_index, span in enumerate(source_spans):
            fragment = stored.text[span.start_offset : span.end_offset]
            if not fragment:
                continue

            separator = "\n\n" if fragments else ""
            available = budget - output_length - len(separator)
            if available <= 0:
                truncated = True
                break

            included_chars = min(len(fragment), available)
            fragment_truncated = included_chars < len(fragment)
            fragments.append(separator + fragment[:included_chars])
            output_length += len(separator) + included_chars
            if included_chars:
                included_ranges.append(
                    (span.start_offset, span.start_offset + included_chars)
                )

            if fragment_truncated:
                truncated = True
                break

            if span_index < len(source_spans) - 1 and (
                output_length >= budget
            ):
                truncated = True
                break

        start_offset = min(
            (start for start, _ in included_ranges),
            default=0,
        )
        end_offset = max(
            (end for _, end in included_ranges),
            default=start_offset,
        )
        return CachedToolOutputWindow(
            text="".join(fragments),
            start_offset=start_offset,
            end_offset=end_offset,
            truncated=truncated,
        )

    def _resolve_budget(self, char_budget: int | None) -> int:
        # 调用方可传入剩余总预算，但不能突破 builder 自身的单窗口上限。
        if char_budget is None:
            return self._char_budget
        return max(1, min(char_budget, self._char_budget))


def _normalize_offset(value: int | None, text_length: int, *, default: int) -> int:
    # 负偏移对齐 Python slice 习惯，最终仍夹在合法原文长度范围内。
    offset = default if value is None else value
    if offset < 0:
        offset += text_length
    return min(max(offset, 0), text_length)
