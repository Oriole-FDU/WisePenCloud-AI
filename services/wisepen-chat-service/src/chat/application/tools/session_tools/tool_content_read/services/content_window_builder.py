from __future__ import annotations

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentChunk,
)

from .models import ToolContentWindow

_DEFAULT_MAX_CHARS = 100_000


class ToolContentWindowBuilder:
    """统一构建块扩展窗口、定位信息和返回长度保护。"""

    __slots__ = ("_max_chars",)

    def __init__(self, *, max_chars: int | None = None) -> None:
        effective_max = max_chars if max_chars is not None else _DEFAULT_MAX_CHARS
        self._max_chars = max(1, int(effective_max))

    def build_expanded_window(
            self,
            stored: StoredToolContent,
            *,
            chunks: tuple[ToolContentChunk, ...],
            center_chunk: int,
            merge_before: int,
            merge_after: int,
    ) -> ToolContentWindow:
        by_index = {chunk.chunk_index: chunk for chunk in chunks}
        start = max(center_chunk - max(merge_before, 0), 0)
        end = min(
            center_chunk + max(merge_after, 0),
            max(by_index.keys(), default=0),
        )
        window_chunks = tuple(
            by_index[index]
            for index in range(start, end + 1)
            if index in by_index
        )
        if window_chunks:
            start = window_chunks[0].chunk_index
            end = window_chunks[-1].chunk_index

        text = self._truncate(
            "\n\n".join(
                text
                for chunk in window_chunks
                if (text := chunk_text(stored, chunk))
            )
        )
        offsets = tuple(
            offset
            for chunk in window_chunks
            for offset in (chunk.start_offset, chunk.end_offset)
            if offset is not None
        )
        page_label, section_path, anchor_labels = _locate_chunks(
            stored,
            window_chunks,
        )

        return ToolContentWindow(
            text=text,
            start_offset=min(offsets) if offsets else None,
            end_offset=max(offsets) if offsets else None,
            center_chunk=center_chunk,
            chunk_start=start,
            chunk_end=end,
            page_label=page_label,
            section_path=section_path,
            anchor_labels=anchor_labels,
        )

    def build_range_window(
            self,
            stored: StoredToolContent,
            *,
            start: int | None,
            end: int | None,
    ) -> ToolContentWindow:
        text_length = len(stored.text)
        normalized_start = _normalize_offset(start, text_length, default=0)
        normalized_end = _normalize_offset(end, text_length, default=text_length)
        if normalized_end < normalized_start:
            raise ValueError("end must not precede start")

        normalized_end = min(normalized_end, normalized_start + self._max_chars)
        chunks = tuple(
            chunk
            for chunk in stored.chunks
            if chunk.start_offset is not None
            and chunk.end_offset is not None
            and chunk.start_offset < normalized_end
            and chunk.end_offset > normalized_start
        )
        page_label, section_path, anchor_labels = _locate_chunks(stored, chunks)

        return ToolContentWindow(
            text=stored.text[normalized_start:normalized_end],
            start_offset=normalized_start,
            end_offset=normalized_end,
            page_label=page_label,
            section_path=section_path,
            anchor_labels=anchor_labels,
        )

    def _truncate(self, text: str) -> str:
        if len(text) <= self._max_chars:
            return text
        return text[: self._max_chars].rstrip() + "\n...[truncated]"


def chunk_text(stored: StoredToolContent, chunk: ToolContentChunk) -> str:
    """根据持久化 offset 读取单个 chunk 的正文。"""
    if chunk.start_offset is None or chunk.end_offset is None:
        return ""
    return stored.text[chunk.start_offset: chunk.end_offset].strip()


def _normalize_offset(value: int | None, text_length: int, *, default: int) -> int:
    offset = default if value is None else value
    if offset < 0:
        offset += text_length
    return min(max(offset, 0), text_length)


def _locate_chunks(
        stored: StoredToolContent,
        chunks: tuple[ToolContentChunk, ...],
) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    section_path = next(
        (
            tuple(str(item) for item in chunk.section_path if str(item))
            for chunk in chunks
            if chunk.section_path
        ),
        (),
    )
    anchor_labels = tuple(
        dict.fromkeys(
            text
            for chunk in chunks
            for name in chunk.anchor_labels
            if (text := str(name).strip())
        )
    )
    return _page_label(stored, chunks), section_path, anchor_labels


def _page_label(
        stored: StoredToolContent,
        chunks: tuple[ToolContentChunk, ...],
) -> str | None:
    for chunk in chunks:
        if chunk.page_label:
            return chunk.page_label

    if not chunks or stored.index is None:
        return None

    target_indices = {chunk.chunk_index for chunk in chunks}
    candidate_page: tuple[int, str] | None = None
    for entry in stored.index.entries:
        if entry.locator_kind != "page":
            continue
        overlap = tuple(
            index for index in entry.chunk_indices if index in target_indices
        )
        if not overlap:
            continue
        page_label = (entry.page_label or "").strip()
        if not page_label:
            continue
        first_overlap = min(overlap)
        if candidate_page is None or first_overlap < candidate_page[0]:
            candidate_page = first_overlap, page_label

    return candidate_page[1] if candidate_page is not None else None
