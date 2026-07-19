from __future__ import annotations

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentStore,
)

from .models import ToolContentReadFailure


class ToolContentLoader:
    """从 ToolContentStore 读取当前会话可见的缓存内容。"""

    __slots__ = ("_store",)

    def __init__(self, *, store: ToolContentStore) -> None:
        self._store = store

    async def load_one(
            self,
            *,
            content_id: str,
            session_id: str,
    ) -> tuple[str, StoredToolContent] | None:
        stored = await self._store.get(content_id=content_id, session_id=session_id)
        return (content_id, stored) if stored is not None else None

    async def load_many(
            self,
            *,
            content_ids: tuple[str, ...],
            session_id: str,
    ) -> tuple[
        tuple[tuple[str, StoredToolContent], ...],
        tuple[ToolContentReadFailure, ...],
    ]:
        stored_items: list[tuple[str, StoredToolContent]] = []
        failed: list[ToolContentReadFailure] = []

        for content_id in content_ids:
            try:
                loaded = await self.load_one(
                    content_id=content_id,
                    session_id=session_id,
                )
            except Exception as exc:
                failed.append(
                    ToolContentReadFailure(
                        content_id=content_id,
                        reason=type(exc).__name__,
                    )
                )
                continue

            if loaded is None:
                failed.append(
                    ToolContentReadFailure(
                        content_id=content_id,
                        reason="content_not_found",
                    )
                )
                continue

            stored_items.append(loaded)

        return tuple(stored_items), tuple(failed)
