from __future__ import annotations

from collections.abc import Iterable

from chat.application.tools.common.tool_content_store.models import StoredToolContent, ToolContentChunk
from chat.application.tools.session_tools.tool_content_read.models import ToolContentWindow
from chat.application.tools.tool_settings import tool_settings

# 聚合窗口的最大允许硬字符上限，超出则执行安全裁剪
MAX_TOOL_CONTENT_WINDOW_CHARS = tool_settings.TOOL_CONTENT_READ_MAX_WINDOW_CHARS


class ToolContentWindowBuilder:
    """无状态工具内容滑动窗口构建器，统一处理块聚合、文本拼接与截断保护。"""

    __slots__ = ()

    @staticmethod
    def expand(
            stored: StoredToolContent,
            *,
            center_chunk: int,
            merge_before: int,
            merge_after: int,
    ) -> ToolContentWindow:
        """以核心块为中心，向两侧滑动混叠相邻分块文本，生成高内聚的上下文窗口。"""
        by_index = {c.chunk_index: c for c in stored.chunks}

        # 1. 换算滑动覆盖的块索引边界
        start_idx = max(center_chunk - max(merge_before, 0), 0)
        end_idx = min(center_chunk + max(merge_after, 0), max(by_index.keys(), default=0))

        # 2. 提取有效分块矩阵序列
        chunks = tuple(by_index[idx] for idx in range(start_idx, end_idx + 1) if idx in by_index)

        # 3. 聚合清洗并拼接各分块文本段落
        text = "\n\n".join(
            ToolContentWindowBuilder.chunk_text(stored, c)
            for c in chunks
            if ToolContentWindowBuilder.chunk_text(stored, c)
        )

        # 4. 超限硬截断保护
        if len(text) > MAX_TOOL_CONTENT_WINDOW_CHARS:
            text = text[:MAX_TOOL_CONTENT_WINDOW_CHARS].rstrip() + "\n...[truncated]"

        # 5. 反向追溯计算在物理原文中的绝对字符偏置范围
        offsets = tuple(
            offset
            for c in chunks
            for offset in (c.start_offset, c.end_offset)
            if offset is not None
        )

        locator = ToolContentWindowBuilder.locator(stored, chunks)
        return ToolContentWindow(
            text=text,
            start_offset=min(offsets) if offsets else None,
            end_offset=max(offsets) if offsets else None,
            center_chunk=center_chunk,
            chunk_start=start_idx,
            chunk_end=end_idx,
            page=locator["page"],
            paragraph_title=locator["paragraph_title"],
            section_path=locator["section_path"],
            anchor_names=locator["anchor_names"],
        )

    @staticmethod
    def chunk_text(stored: StoredToolContent, chunk: ToolContentChunk) -> str:
        """从底层的原始大文本中，基于物理偏置闭包安全裁剪单个块的内容。"""
        if chunk.start_offset is None or chunk.end_offset is None:
            return ""
        return stored.text[chunk.start_offset:chunk.end_offset].strip()

    @staticmethod
    def locator(
            stored: StoredToolContent,
            chunks: Iterable[ToolContentChunk],
    ) -> dict[str, str | tuple[str, ...] | None]:
        """从 chunk 与索引元数据中提取稳定定位信息。"""
        chunk_list = tuple(chunks)
        section_path = ToolContentWindowBuilder._section_path(chunk_list)
        return {
            "page": ToolContentWindowBuilder._page_name(stored, chunk_list),
            "paragraph_title": section_path[-1] if section_path else None,
            "section_path": section_path,
            "anchor_names": ToolContentWindowBuilder._anchor_names(chunk_list),
        }

    @staticmethod
    def _section_path(chunks: tuple[ToolContentChunk, ...]) -> tuple[str, ...]:
        for chunk in chunks:
            if chunk.section_path:
                return tuple(str(item) for item in chunk.section_path if str(item))
        return ()

    @staticmethod
    def _anchor_names(chunks: tuple[ToolContentChunk, ...]) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for chunk in chunks:
            for name in chunk.anchor_names:
                text = str(name).strip()
                if text:
                    seen.setdefault(text, None)
        return tuple(seen)

    @staticmethod
    def _page_name(
            stored: StoredToolContent,
            chunks: tuple[ToolContentChunk, ...],
    ) -> str | None:
        if not chunks or stored.index is None:
            return None

        target_indices = {chunk.chunk_index for chunk in chunks}
        candidate_page: tuple[int, str] | None = None
        for entry in stored.index.entries:
            if not entry.name.startswith("page:"):
                continue
            overlap = tuple(idx for idx in entry.chunk_indices if idx in target_indices)
            if not overlap:
                continue
            page_name = entry.name.split(":", 1)[1].strip()
            if not page_name:
                continue
            first_overlap = min(overlap)
            if candidate_page is None or first_overlap < candidate_page[0]:
                candidate_page = (first_overlap, page_name)

        return candidate_page[1] if candidate_page is not None else None
