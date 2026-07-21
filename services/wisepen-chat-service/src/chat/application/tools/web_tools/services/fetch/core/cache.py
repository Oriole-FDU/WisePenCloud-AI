from __future__ import annotations

from typing import Protocol

from .models import WebContentCacheMode, WebContentCacheValue


class WebContentCacheRepository(Protocol):
    async def get_value(
        self,
        *,
        user_id: str,
        url: str,
        cache_mode: WebContentCacheMode,
    ) -> WebContentCacheValue | None:
        ...

    async def set_value(self, value: WebContentCacheValue) -> None:
        ...
