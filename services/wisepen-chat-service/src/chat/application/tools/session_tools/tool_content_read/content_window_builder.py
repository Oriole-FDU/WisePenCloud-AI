from __future__ import annotations

from collections.abc import Iterable

from chat.application.tools.common.tool_content_store.core.models import (
    StoredToolContent,
    ToolContentChunk,
)
from chat.application.tools.session_tools.tool_content_read.models import (
    ToolContentWindow,
)

_DEFAULT_TOOL_CONTENT_WINDOW_MAX_CHARS = 100_000


class ToolContentWindowBuilder:
    """无状态工具内容滑动窗口构建器，统一处理块聚合、文本拼接与截断保护。"""

    __slots__ = ("_max_window_chars",)

    def __init__(
        self,
        *,
        max_window_chars: int | None = None,
    ) -> None:
        effective_max = (
            max_window_chars
            if max_window_chars is not None
            else _DEFAULT_TOOL_CONTENT_WINDOW_MAX_CHARS
        )
        self._max_window_chars = max(1, int(effective_max))

    def expand(
        self,
        stored: StoredToolContent,
        *,
        chunks: tuple[ToolContentChunk, ...],
        center_chunk: int,
        merge_before: int,
        merge_after: int,
    ) -> ToolContentWindow:
        """以核心块为中心，向两侧滑动混叠相邻分块文本，生成高内聚的上下文窗口。"""
        by_index = {c.chunk_index: c for c in chunks}

        start_idx = max(center_chunk - max(merge_before, 0), 0)
        end_idx = min(
            center_chunk + max(merge_after, 0), max(by_index.keys(), default=0)
        )

        window_chunks = tuple(
            by_index[idx]
            for idx in range(start_idx, end_idx + 1)
            if idx in by_index
        )
        if window_chunks:
            start_idx = window_chunks[0].chunk_index
            end_idx = window_chunks[-1].chunk_index

        parts = tuple(
            text
            for c in window_chunks
            if (text := ToolContentWindowBuilder.chunk_text(stored, c))
        )
        text = self.truncate("\n\n".join(parts))

        offsets = tuple(
            offset
            for c in window_chunks
            for offset in (c.start_offset, c.end_offset)
            if offset is not None
        )

        locator = ToolContentWindowBuilder.locator(stored, window_chunks)
        return ToolContentWindow(
            text=text,
            start_offset=min(offsets) if offsets else None,
            end_offset=max(offsets) if offsets else None,
            center_chunk=center_chunk,
            chunk_start=start_idx,
            chunk_end=end_idx,
            page_label=locator["page_label"],
            section_title=locator["section_title"],
            section_path=locator["section_path"],
            anchor_labels=locator["anchor_labels"],
        )

    def truncate(self, text: str) -> str:
        """只限制工具本次返回的窗口文本，不修改 ToolContentStore 中的缓存正文。"""
        if len(text) <= self._max_window_chars:
            return text
        return text[:self._max_window_chars].rstrip() + "\n...[truncated]"

    @staticmethod
    def chunk_text(stored: StoredToolContent, chunk: ToolContentChunk) -> str:
        """从底层的原始大文本中，基于物理偏置闭包安全裁剪单个块的内容。"""
        if chunk.start_offset is None or chunk.end_offset is None:
            return ""
        return stored.text[chunk.start_offset : chunk.end_offset].strip()

    @staticmethod
    def locator(
        stored: StoredToolContent,
        chunks: Iterable[ToolContentChunk],
    ) -> dict[str, str | tuple[str, ...] | None]:
        """从 chunk 与索引元数据中提取稳定定位信息。"""
        chunk_list = tuple(chunks)
        section_path = ToolContentWindowBuilder._section_path(chunk_list)
        return {
            "page_label": ToolContentWindowBuilder._page_label(stored, chunk_list),
            "section_title": section_path[-1] if section_path else None,
            "section_path": section_path,
            "anchor_labels": ToolContentWindowBuilder._anchor_labels(chunk_list),
        }

    @staticmethod
    def _section_path(chunks: tuple[ToolContentChunk, ...]) -> tuple[str, ...]:
        for chunk in chunks:
            if chunk.section_path:
                return tuple(str(item) for item in chunk.section_path if str(item))
        return ()

    @staticmethod
    def _anchor_labels(chunks: tuple[ToolContentChunk, ...]) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for chunk in chunks:
            for name in chunk.anchor_labels:
                text = str(name).strip()
                if text:
                    seen.setdefault(text, None)
        return tuple(seen)

    @staticmethod
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
            overlap = tuple(idx for idx in entry.chunk_indices if idx in target_indices)
            if not overlap:
                continue
            page_label = (entry.page_label or "").strip()
            if not page_label:
                continue
            first_overlap = min(overlap)
            if candidate_page is None or first_overlap < candidate_page[0]:
                candidate_page = (first_overlap, page_label)

        return candidate_page[1] if candidate_page is not None else None
