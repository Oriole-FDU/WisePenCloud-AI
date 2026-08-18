from __future__ import annotations

from dataclasses import dataclass, field

from common.utils.document import SourceSpan

from chat.application.tools.core.output_cache.cache_store import (
    StoredToolContent as StoredCachedToolOutput,
)
from chat.application.tools.core.output_cache.cache_store import (
    ToolContentChunk as CachedToolOutputChunk,
)


@dataclass(slots=True)
class CachedToolOutputWindow:
    text: str
    start_offset: int
    end_offset: int
    source_spans: list[SourceSpan] = field(default_factory=list)
    page_labels: list[str] = field(default_factory=list)
    section_ids: list[str] = field(default_factory=list)
    anchor_labels: list[str] = field(default_factory=list)
    truncated: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


class CachedToolOutputWindowBuilder:
    __slots__ = ("_char_budget",)

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
            # 空区间不是错误，返回空窗口即可，方便调用方用偏移继续探测。
            normalized_end = normalized_start
            truncated = False
        else:
            # 窗口预算只限制本次返回长度，不改变调用方请求的原始 end 语义。
            budget = self._resolve_budget(char_budget)
            requested_length = requested_end - normalized_start
            included_chars = min(requested_length, budget)
            truncated = requested_length > budget
            normalized_end = normalized_start + included_chars
        return self._continuous_window(
            stored,
            start=normalized_start,
            end=normalized_end,
            truncated=truncated,
        )

    def build_source_window(
        self,
        stored: StoredCachedToolOutput,
        *,
        chunk: CachedToolOutputChunk,
        char_budget: int | None = None,
    ) -> CachedToolOutputWindow:
        # semantic search 使用 chunk 的 source_spans 回读原文，避免把索引时的摘要文本当作来源。
        budget = self._resolve_budget(char_budget)
        fragments: list[str] = []
        included_spans: list[SourceSpan] = []
        truncated = False
        for span_index, span in enumerate(chunk.source_spans):
            # 多个 span 之间用空行拼接；拼接符也要计入本次窗口预算。
            prefix = "\n\n".join(fragments)
            if prefix:
                prefix += "\n\n"
            available = budget - len(prefix)
            if available <= 0:
                truncated = True
                break

            fragment = stored.text[span.start_offset : span.end_offset]
            included_chars = min(len(fragment), available)
            fragment_truncated = len(fragment) > available
            fragment = fragment[:included_chars]
            if not fragment and span.start_offset < span.end_offset:
                # 当前 span 有内容但预算已经无法容纳任何字符，标记截断后结束。
                truncated = True
                break

            fragments.append(fragment)
            included_spans.append(
                SourceSpan(span.start_offset, span.start_offset + included_chars)
            )
            if fragment_truncated or (
                span_index < len(chunk.source_spans) - 1
                and len("\n\n".join(fragments)) >= budget
            ):
                truncated = True
                break

        start = min((span.start_offset for span in included_spans), default=0)
        end = max((span.end_offset for span in included_spans), default=0)
        page_labels, section_ids, anchor_labels = _structure_labels(
            stored,
            included_spans,
        )
        return CachedToolOutputWindow(
            text="\n\n".join(fragments),
            start_offset=start,
            end_offset=end,
            source_spans=included_spans,
            page_labels=page_labels,
            section_ids=section_ids,
            anchor_labels=anchor_labels,
            truncated=truncated,
            metadata=dict(stored.metadata),
        )

    def _resolve_budget(self, char_budget: int | None) -> int:
        # 调用方可传入剩余总预算，但不能突破 builder 自身的单窗口上限。
        if char_budget is None:
            return self._char_budget
        return max(1, min(char_budget, self._char_budget))

    def _continuous_window(
        self,
        stored: StoredCachedToolOutput,
        *,
        start: int,
        end: int,
        truncated: bool,
    ) -> CachedToolOutputWindow:
        source_spans = [SourceSpan(start, end)] if start < end else []
        page_labels, section_ids, anchor_labels = _structure_labels(
            stored,
            source_spans,
        )
        return CachedToolOutputWindow(
            text=stored.text[start:end],
            start_offset=start,
            end_offset=end,
            source_spans=source_spans,
            page_labels=page_labels,
            section_ids=section_ids,
            anchor_labels=anchor_labels,
            truncated=truncated,
            metadata=dict(stored.metadata),
        )


def _normalize_offset(value: int | None, text_length: int, *, default: int) -> int:
    # 负偏移对齐 Python slice 习惯，最终仍夹在合法原文长度范围内。
    offset = default if value is None else value
    if offset < 0:
        offset += text_length
    return min(max(offset, 0), text_length)


def _structure_labels(
    stored: StoredCachedToolOutput,
    source_spans: list[SourceSpan],
) -> tuple[list[str], list[str], list[str]]:
    """只投影实际返回 span 覆盖到的具体结构身份。"""
    page_labels = list(
        dict.fromkeys(
            page.page_label
            for page in stored.pages
            if _overlaps_any(page.source_span, source_spans)
        )
    )
    section_ids = list(
        dict.fromkeys(
            section.section_id
            for section in stored.sections
            if _overlaps_any(section.own_span, source_spans)
        )
    )
    anchor_labels = list(
        dict.fromkeys(
            anchor.label
            for anchor in stored.anchors
            if _overlaps_any(anchor.source_span, source_spans)
        )
    )
    return page_labels, section_ids, anchor_labels


def _overlaps_any(span: SourceSpan, others: list[SourceSpan]) -> bool:
    return any(
        span.start_offset < other.end_offset
        and span.end_offset > other.start_offset
        for other in others
    )
