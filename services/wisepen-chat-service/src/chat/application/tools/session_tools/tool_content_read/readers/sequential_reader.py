from __future__ import annotations

from chat.application.tools.common.tool_content_store.core.models import (
    StoredToolContent,
)
from chat.application.tools.session_tools.tool_content_read.content_loader import (
    ToolContentLoader,
)
from chat.application.tools.session_tools.tool_content_read.content_window_builder import (
    ToolContentWindowBuilder,
)
from chat.application.tools.session_tools.tool_content_read.models import (
    ToolContentSequentialReadResult,
    ToolContentWindow,
)


class SequentialReader:
    """单文档 offset/limit 顺序读取 reader。"""

    __slots__ = ("_loader", "_window_builder")

    def __init__(
        self,
        *,
        loader: ToolContentLoader,
        window_builder: ToolContentWindowBuilder,
    ) -> None:
        self._loader = loader
        self._window_builder = window_builder

    async def read(
        self,
        *,
        content_id: str,
        session_id: str,
        offset: int,
        limit: int,
    ) -> ToolContentSequentialReadResult:
        loaded = await self._loader.load_one(
            content_id=content_id,
            session_id=session_id,
        )
        if loaded is None:
            return ToolContentSequentialReadResult(
                content_id=content_id,
                status="failed",
                reason="content_not_found",
            )

        canonical_id, stored = loaded
        return ToolContentSequentialReadResult(
            content_id=canonical_id,
            status="success",
            window=self._build_window(
                stored=stored,
                offset=offset,
                limit=limit,
            ),
        )

    def _build_window(
        self,
        *,
        stored: StoredToolContent,
        offset: int,
        limit: int,
    ) -> ToolContentWindow:
        safe_offset = max(offset, 0)
        safe_limit = max(limit, 0)
        end = min(len(stored.text), safe_offset + safe_limit)
        chunks = tuple(
            chunk
            for chunk in stored.chunks
            if chunk.start_offset is not None
            and chunk.end_offset is not None
            and chunk.start_offset < end
            and chunk.end_offset > safe_offset
        )
        locator = ToolContentWindowBuilder.locator(stored, chunks)
        return ToolContentWindow(
            text=self._window_builder.truncate(stored.text[safe_offset:end]),
            start_offset=safe_offset,
            end_offset=end,
            page_label=locator["page_label"],
            section_title=locator["section_title"],
            section_path=locator["section_path"],
            anchor_labels=locator["anchor_labels"],
        )
