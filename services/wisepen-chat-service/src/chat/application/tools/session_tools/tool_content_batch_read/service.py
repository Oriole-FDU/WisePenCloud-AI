from __future__ import annotations

from chat.application.tools.common.tool_content_store import ToolContentStore
from chat.application.tools.session_tools.tool_content_batch_read.models import (
    ToolContentBatchReadItemRequest,
    ToolContentBatchReadItemResult,
    ToolContentBatchReadRequest,
)
from chat.application.tools.session_tools.utils.content_window_builder import ToolContentWindowBuilder


class ToolContentBatchReadService:
    """按 content 绑定 chunk_indices 的批量窗口读取服务。"""

    __slots__ = ("_store",)

    def __init__(self, *, store: ToolContentStore) -> None:
        self._store = store

    async def read(
        self,
        *,
        request: ToolContentBatchReadRequest,
        session_id: str,
    ) -> tuple[ToolContentBatchReadItemResult, ...]:
        """批量读取多个 content 的显式 chunk 窗口。"""
        items: list[ToolContentBatchReadItemResult] = []
        for item in request.items:
            items.append(await self._read_one(item=item, request=request, session_id=session_id))

        return tuple(items)

    async def _read_one(
        self,
        *,
        item: ToolContentBatchReadItemRequest,
        request: ToolContentBatchReadRequest,
        session_id: str,
    ) -> ToolContentBatchReadItemResult:
        """读取单个 content；单项失败转为 failed item。"""
        try:
            canonical_content_id, _ = await self._store.canonicalize_content_id(
                content_id=item.content_id,
                session_id=session_id,
            )
            stored = await self._store.get(content_id=canonical_content_id, session_id=session_id)
            if stored is None:
                return ToolContentBatchReadItemResult(
                    content_id=canonical_content_id,
                    status="failed",
                    reason="content_not_found",
                )

            available = {chunk.chunk_index for chunk in stored.chunks}
            # 去重但保留模型请求顺序；chunk_index 只在当前 content 内解释。
            requested = tuple(dict.fromkeys(item.chunk_indices))
            centers = tuple(index for index in requested if index in available)
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
                content_id=canonical_content_id,
                status="success",
                windows=windows,
            )
        except Exception as e:
            return ToolContentBatchReadItemResult(
                content_id=item.content_id,
                status="failed",
                reason=e.__class__.__name__,
            )
