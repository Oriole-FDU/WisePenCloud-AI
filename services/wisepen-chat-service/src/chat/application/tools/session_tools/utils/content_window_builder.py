from __future__ import annotations

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

        return ToolContentWindow(
            text=text,
            start_offset=min(offsets) if offsets else None,
            end_offset=max(offsets) if offsets else None,
            center_chunk=center_chunk,
            chunk_start=start_idx,
            chunk_end=end_idx,
        )

    @staticmethod
    def chunk_text(stored: StoredToolContent, chunk: ToolContentChunk) -> str:
        """从底层的原始大文本中，基于物理偏置闭包安全裁剪单个块的内容。"""
        if chunk.start_offset is None or chunk.end_offset is None:
            return ""
        return stored.text[chunk.start_offset:chunk.end_offset].strip()