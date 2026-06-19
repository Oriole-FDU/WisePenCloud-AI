from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from chat.application.tools.web_tools.web_content_cache.refresh_queue import (
    WebContentCacheRefreshJob,
    WebContentCacheRefreshTaskPublisher,
)


class ArqWebContentCacheRefreshTaskPublisher(WebContentCacheRefreshTaskPublisher):
    """基于 Arq 的网页内容缓存 stale 刷新任务发布器。"""

    __slots__ = ("_pool", "_queue_name", "_redis_settings")

    def __init__(
        self,
        *,
        redis_url: str,
        queue_name: str = "wisepen:web_content_cache:refresh",
    ) -> None:
        self._redis_settings = RedisSettings.from_dsn(redis_url)
        self._queue_name = queue_name
        self._pool: ArqRedis | None = None

    async def enqueue(self, job: WebContentCacheRefreshJob) -> None:
        pool = await self._get_pool()
        await pool.enqueue_job(
            job.name,
            job.payload,
            _job_id=job.job_id,
            _queue_name=self._queue_name,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self) -> ArqRedis:
        if self._pool is None:
            self._pool = await create_pool(
                self._redis_settings,
                default_queue_name=self._queue_name,
            )
        return self._pool
