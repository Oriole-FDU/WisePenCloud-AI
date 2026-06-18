from __future__ import annotations

from chat.application.tools.common.tool_content_store import ToolContentStore
from chat.application.tools.session_tools.tool_content_batch_read.models import (
    ToolContentBatchReadItemRequest,
    ToolContentBatchReadItemResult,
    ToolContentBatchReadRequest,
)
from chat.application.tools.session_tools.utils.content_window_builder import ToolContentWindowBuilder


class ToolContentBatchReadService:
    """批量工具内容分块读取服务，支持点对点的显式数据块切片提取。"""

    __slots__ = ("_store",)

    def __init__(self, *, store: ToolContentStore) -> None:
        self._store = store

    async def read(
            self,
            *,
            request: ToolContentBatchReadRequest,
            session_id: str,
    ) -> tuple[ToolContentBatchReadItemResult, ...]:
        """批量读取多个内容凭证下显式指定的多个数据块窗口。"""
        items: list[ToolContentBatchReadItemResult] = []
        for item in request.items:
            res = await self._read_one(item=item, request=request, session_id=session_id)
            items.append(res)

        return tuple(items)

    async def _read_one(
            self,
            *,
            item: ToolContentBatchReadItemRequest,
            request: ToolContentBatchReadRequest,
            session_id: str,
    ) -> ToolContentBatchReadItemResult:
        """读取单个凭证的数据块。单项异常降级为 failed，不破坏批处理连贯性。"""
        try:
            # 自动规整并换算可能的重定向收据
            canonical_id, _ = await self._store.canonicalize_content_id(
                content_id=item.content_id,
                session_id=session_id,
            )
            stored = await self._store.get(content_id=canonical_id, session_id=session_id)

            if stored is None:
                return ToolContentBatchReadItemResult(
                    content_id=canonical_id,
                    status="failed",
                    reason="content_not_found",
                )

            # 提取现存可用分块索引集合进行边界防御
            available = {c.chunk_index for c in stored.chunks}

            # 有序去重请求的块索引，并剔除越界无效索引
            requested = tuple(dict.fromkeys(item.chunk_indices))
            centers = tuple(idx for idx in requested if idx in available)

            # 批量构建滑动合并窗口
            windows = tuple(
                ToolContentWindowBuilder.expand(
                    stored,
                    center_chunk=center,
                    merge_before=request.merge_before,
                    merge_after=request.merge_after,
                )
                for center in centers
            )

            return ToolContentBatchReadItemResult(
                content_id=canonical_id,
                status="success",
                windows=windows,
            )

        except Exception as e:
            return ToolContentBatchReadItemResult(
                content_id=item.content_id,
                status="failed",
                reason=e.__class__.__name__,
            )
