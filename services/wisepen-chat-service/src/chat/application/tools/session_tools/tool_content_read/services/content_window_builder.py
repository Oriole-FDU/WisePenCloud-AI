from __future__ import annotations

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentChunk,
)
from common.utils.chunkers import SourceSpan

from .models import ToolContentWindow

_DEFAULT_MAX_CHARS = 8000


class ToolContentWindowBuilder:
    """从权威原文构建有长度保护的连续或非连续窗口。"""

    __slots__ = ("_max_chars",)

    def __init__(self, *, max_chars: int | None = None) -> None:
        self._max_chars = max(1, int(max_chars or _DEFAULT_MAX_CHARS))

    def build_range_window(
        self,
        stored: StoredToolContent,
        *,
        start: int | None,
        end: int | None,
    ) -> ToolContentWindow:
        text_length = len(stored.text)
        normalized_start = _normalize_offset(start, text_length, default=0)
        requested_end = _normalize_offset(end, text_length, default=text_length)
        if requested_end <= normalized_start:
            normalized_end = normalized_start
        else:
            normalized_end = min(requested_end, normalized_start + self._max_chars)
        return self._continuous_window(
            stored,
            start=normalized_start,
            end=normalized_end,
            truncated=normalized_end < requested_end,
        )

    def build_source_window(
        self,
        stored: StoredToolContent,
        *,
        chunk: ToolContentChunk,
    ) -> ToolContentWindow:
        fragments: list[str] = []
        included_spans: list[SourceSpan] = []
        remaining = self._max_chars
        truncated = False
        for span in chunk.source_spans:
            separator = 2 if fragments else 0
            if remaining <= separator:
                truncated = True
                break
            fragment = stored.text[span.start_offset : span.end_offset]
            available = remaining - separator
            if len(fragment) > available:
                fragment = fragment[:available]
                truncated = True
            if fragments:
                remaining -= 2
            fragments.append(fragment)
            included_spans.append(
                SourceSpan(span.start_offset, span.start_offset + len(fragment))
            )
            remaining -= len(fragment)
            if truncated:
                break

        start = min((span.start_offset for span in included_spans), default=0)
        end = max((span.end_offset for span in included_spans), default=0)
        return ToolContentWindow(
            text="\n\n".join(fragments),
            start_offset=start,
            end_offset=end,
            source_spans=tuple(included_spans),
            locator_names=_chunk_locator_names(chunk),
            page_labels=chunk.page_labels,
            section_paths=chunk.section_paths,
            anchor_labels=chunk.anchor_labels,
            truncated=truncated,
            metadata=dict(stored.metadata),
        )

    def _continuous_window(
        self,
        stored: StoredToolContent,
        *,
        start: int,
        end: int,
        truncated: bool,
    ) -> ToolContentWindow:
        return ToolContentWindow(
            text=stored.text[start:end],
            start_offset=start,
            end_offset=end,
            source_spans=(SourceSpan(start, end),) if start < end else (),
            truncated=truncated,
            metadata=dict(stored.metadata),
        )


def chunk_text(stored: StoredToolContent, chunk: ToolContentChunk) -> str:
    return "\n\n".join(
        stored.text[span.start_offset : span.end_offset].strip()
        for span in chunk.source_spans
    )


def _normalize_offset(value: int | None, text_length: int, *, default: int) -> int:
    offset = default if value is None else value
    if offset < 0:
        offset += text_length
    return min(max(offset, 0), text_length)


def _chunk_locator_names(chunk: ToolContentChunk) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *(f"section:{' > '.join(path)}" for path in chunk.section_paths),
                *(f"page:{label}" for label in chunk.page_labels),
                *(f"anchor:{label}" for label in chunk.anchor_labels),
            )
        )
    )
