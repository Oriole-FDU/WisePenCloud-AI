from __future__ import annotations

from typing import Protocol

from .models import StoredToolContent


class ToolContentRepository(Protocol):
    """ToolContent 持久化协议，定义 put/get 两个核心操作。"""

    async def put(self, stored: StoredToolContent) -> None:
        """写入 ToolContent。"""
        ...

    async def get(self, content_id: str) -> StoredToolContent | None:
        """按 content_id 读取 ToolContent，不存在则返回 None。"""
        ...
