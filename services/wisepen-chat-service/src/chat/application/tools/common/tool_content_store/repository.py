from __future__ import annotations

from typing import Protocol

from .models import StoredToolContent


class ToolContentRepository(Protocol):
    """ToolContent 持久化边界。"""

    async def put(self, stored: StoredToolContent) -> None: ...

    async def get(self, content_id: str) -> StoredToolContent | None: ...
