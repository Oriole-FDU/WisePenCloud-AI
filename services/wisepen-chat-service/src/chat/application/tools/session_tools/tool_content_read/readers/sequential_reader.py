from __future__ import annotations

from ..content_loader import ToolContentLoader
from ..content_window_builder import ToolContentWindowBuilder
from ..models import ToolContentSequentialReadResult


class SequentialReader:
    """按 offset/limit 顺序读取单个文档。"""

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
                reason="content_not_found",
            )

        canonical_id, stored = loaded
        return ToolContentSequentialReadResult(
            content_id=canonical_id,
            window=self._window_builder.build_sequential_window(
                stored,
                offset=offset,
                limit=limit,
            ),
        )
