from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chat.application.tools.web_tools.common.cache import WebContentCacheValue


class WebContentCacheRepository(ABC):
    """Web 内容缓存的持久化边界。"""

    @abstractmethod
    async def get_value(
        self,
        *,
        url: str,
        cache_variant: str = "",
    ) -> WebContentCacheValue | None:
        pass

    @abstractmethod
    async def set_value(self, value: WebContentCacheValue) -> None:
        pass
