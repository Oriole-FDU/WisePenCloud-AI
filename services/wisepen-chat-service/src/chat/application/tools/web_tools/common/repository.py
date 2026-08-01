from __future__ import annotations

from typing import Protocol

from .models import WebContentCacheValue


class WebContentCacheRepository(Protocol):
    async def get_value(
        self,
        *,
        url: str,
        cache_variant: str = "",
    ) -> WebContentCacheValue | None: ...

    async def set_value(self, value: WebContentCacheValue) -> None: ...
