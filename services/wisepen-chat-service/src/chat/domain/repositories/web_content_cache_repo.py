from __future__ import annotations

from abc import ABC, abstractmethod

from chat.application.tools.web_tools.common import WebContentCacheValue


class WebContentCacheRepository(ABC):
    """web cache 的 Redis 持久化边界。"""

    @abstractmethod
    async def get_value(self, *, url: str) -> WebContentCacheValue | None:
        pass

    @abstractmethod
    async def set_value(self, value: WebContentCacheValue) -> None:
        pass
