from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class WebContentCacheValue:
    """URL 级缓存的完整正文；raw_html 仅供 crawl 继续发现链接。"""

    canonical_url: str
    text: str
    expire_at: datetime
    raw_html: str | None = None


class WebContentCacheRepository(ABC):
    """web cache 的 Redis 持久化边界。"""

    @abstractmethod
    async def get_value(self, *, url: str) -> WebContentCacheValue | None:
        pass

    @abstractmethod
    async def set_value(self, value: WebContentCacheValue) -> None:
        pass
