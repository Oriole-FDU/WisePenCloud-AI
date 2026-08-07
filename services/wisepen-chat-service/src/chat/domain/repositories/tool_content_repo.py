from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chat.application.tools.common.tool_content_store.models import StoredToolContent


class ToolContentRepository(ABC):
    """工具内容持久化边界。"""

    @abstractmethod
    async def put(self, stored: StoredToolContent) -> None:
        pass

    @abstractmethod
    async def get(self, content_id: str) -> StoredToolContent | None:
        pass
