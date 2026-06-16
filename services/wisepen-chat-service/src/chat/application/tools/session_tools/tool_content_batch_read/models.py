from __future__ import annotations

from dataclasses import dataclass

from chat.application.tools.session_tools.tool_content_read.models import ToolContentWindow


@dataclass(frozen=True, slots=True)
class ToolContentBatchReadItemRequest:
    """单个 content 的显式 chunk 批量读取请求。"""

    content_id: str  # 调用方传入的 cnt_* 内容 ID
    chunk_indices: tuple[int, ...]  # 当前 content 内要展开的中心 chunk 序号集合


@dataclass(frozen=True, slots=True)
class ToolContentBatchReadRequest:
    """tool_content_batch_read 内部请求。"""

    items: tuple[ToolContentBatchReadItemRequest, ...]  # 每个 item 自己绑定 content_id 与 chunk_indices
    merge_before: int = 0  # 每个中心 chunk 向前合并的 chunk 数
    merge_after: int = 0  # 每个中心 chunk 向后合并的 chunk 数


@dataclass(frozen=True, slots=True)
class ToolContentBatchReadItemResult:
    """单个 content 的批量窗口读取结果。"""

    content_id: str  # 实际读取的内容 ID（可能经过重定向）
    status: str  # success 或 failed
    windows: tuple[ToolContentWindow, ...] = ()  # 展开得到的窗口列表
    reason: str | None = None  # 单项失败原因
