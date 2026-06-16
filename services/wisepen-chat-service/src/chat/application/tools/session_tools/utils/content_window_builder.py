from __future__ import annotations

from chat.application.tools.common.tool_content_store.models import StoredToolContent, ToolContentChunk
from chat.application.tools.session_tools.tool_content_read.models import ToolContentWindow

# 单个窗口最大字符数，超过则截断
MAX_TOOL_CONTENT_WINDOW_CHARS = 20_000


class ToolContentWindowBuilder:
    """ToolContent chunk 窗口构造器。

    显式 chunk 展开、ranked_expand 和 regex_match 都需要同一套窗口拼接逻辑。
    这里保持为无状态命名空间，避免不同工具各自实现窗口边界和截断规则。
    """

    __slots__ = ()

    @staticmethod
    def expand(
        stored: StoredToolContent,
        *,
        center_chunk: int,       # 中心 chunk 序号
        merge_before: int,       # 向前合并的 chunk 数
        merge_after: int,        # 向后合并的 chunk 数
    ) -> ToolContentWindow:
        """以 center_chunk 为中心，前后合并 chunks 生成一个窗口。"""
        by_index = {chunk.chunk_index: chunk for chunk in stored.chunks}
        start_index = max(center_chunk - max(merge_before, 0), 0)
        end_index = min(center_chunk + max(merge_after, 0), max(by_index.keys(), default=0))
        chunks = tuple(by_index[index] for index in range(start_index, end_index + 1) if index in by_index)

        # 用双换行拼接 chunk 文本，保持 Markdown 段落边界。
        text = "\n\n".join(
            ToolContentWindowBuilder.chunk_text(stored, chunk)
            for chunk in chunks
            if ToolContentWindowBuilder.chunk_text(stored, chunk)
        )
        if len(text) > MAX_TOOL_CONTENT_WINDOW_CHARS:
            text = text[:MAX_TOOL_CONTENT_WINDOW_CHARS].rstrip() + "\n...[truncated]"

        # 收集窗口覆盖的原文 offset 范围，便于模型后续连续读取或定位。
        offsets = tuple(
            offset
            for chunk in chunks
            for offset in (chunk.start_offset, chunk.end_offset)
            if offset is not None
        )
        return ToolContentWindow(
            text=text,
            start_offset=min(offsets) if offsets else None,
            end_offset=max(offsets) if offsets else None,
            center_chunk=center_chunk,
            chunk_start=start_index,
            chunk_end=end_index,
        )

    @staticmethod
    def chunk_text(stored: StoredToolContent, chunk: ToolContentChunk) -> str:
        """从原始文本中按 offset 截取 chunk 对应的文本片段。"""
        if chunk.start_offset is None or chunk.end_offset is None:
            return ""
        return stored.text[chunk.start_offset:chunk.end_offset].strip()
