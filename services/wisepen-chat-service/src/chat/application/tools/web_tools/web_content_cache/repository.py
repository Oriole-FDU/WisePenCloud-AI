from __future__ import annotations

from typing import Protocol

from datetime import datetime

from .models import (
    WebContentCacheCleanupResult,
    WebContentCacheEntry,
    WebContentCacheMode,
    WebContentCacheValue,
)


class WebContentCacheRepository(Protocol):
    """统一 URL 内容缓存仓储边界。"""

    async def get_entry(
        self,
        *,
        user_id: str,
        url: str,
        cache_mode: WebContentCacheMode | str,
    ) -> WebContentCacheEntry | None:
        ...

    async def get_readable_entry(
        self,
        *,
        user_id: str,
        url: str,
    ) -> WebContentCacheEntry | None:
        ...

    async def set_entry(self, entry: WebContentCacheEntry) -> None:
        ...

    async def get_value(self, *, doc_id: str) -> WebContentCacheValue | None:
        ...

    async def save_value(self, value: WebContentCacheValue) -> str:
        ...

    async def delete_entry(
        self,
        *,
        user_id: str,
        url: str,
        cache_mode: WebContentCacheMode | str,
    ) -> None:
        ...

    async def try_acquire_refresh_lock(
        self,
        *,
        key: str,
        ttl_seconds: int,
    ) -> bool:
        ...

    async def cleanup_inactive_values(
        self,
        *,
        updated_before: datetime,
        batch_size: int,
    ) -> WebContentCacheCleanupResult:
        ...
