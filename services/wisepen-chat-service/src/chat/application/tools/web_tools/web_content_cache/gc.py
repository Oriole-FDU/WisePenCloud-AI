from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Protocol

from common.logger import info, warn

from chat.application.tools.tool_settings import tool_settings
from .models import WebContentCacheCleanupResult


class WebContentCacheCleanupRepository(Protocol):
    async def cleanup_inactive_values(
        self,
        *,
        updated_before: datetime,
        batch_size: int,
    ) -> WebContentCacheCleanupResult:
        ...


class WebContentCacheGcScheduler:
    """定期清理 MongoDB 中不再 active 的 URL 缓存正文。"""

    __slots__ = (
        "_batch_size",
        "_interval_seconds",
        "_retention_seconds",
        "_repository",
        "_task",
    )

    def __init__(
        self,
        *,
        repository: WebContentCacheCleanupRepository,
        interval_seconds: int = tool_settings.WEB_CONTENT_CACHE_CLEANUP_INTERVAL_SECONDS,
        retention_seconds: int = tool_settings.WEB_CONTENT_CACHE_INACTIVE_RETENTION_SECONDS,
        batch_size: int = tool_settings.WEB_CONTENT_CACHE_CLEANUP_BATCH_SIZE,
    ) -> None:
        self._repository = repository
        self._interval_seconds = max(1, int(interval_seconds))
        self._retention_seconds = max(1, int(retention_seconds))
        self._batch_size = max(1, int(batch_size))
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run_loop(),
                name="web-content-cache-gc",
            )
            info(
                "web content cache gc scheduler started.",
                interval_seconds=self._interval_seconds,
                retention_seconds=self._retention_seconds,
                batch_size=self._batch_size,
            )

    async def stop(self) -> None:
        if self._task is None:
            return

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            info("web content cache gc scheduler stopped.")

    async def cleanup_once(self) -> WebContentCacheCleanupResult:
        updated_before = datetime.now(timezone.utc) - timedelta(seconds=self._retention_seconds)
        return await self._repository.cleanup_inactive_values(
            updated_before=updated_before,
            batch_size=self._batch_size,
        )

    async def _run_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._interval_seconds)
                result = await self.cleanup_once()
                info(
                    "web content cache gc finished.",
                    scanned=result.scanned,
                    deleted=result.deleted,
                    active=result.active,
                    failed=result.failed,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                warn("web content cache gc failed.", e=exc)
