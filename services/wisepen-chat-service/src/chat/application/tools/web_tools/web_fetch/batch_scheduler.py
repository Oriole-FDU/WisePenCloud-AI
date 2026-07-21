from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .core.models import WebFetchResult


FetchSlot = WebFetchResult | None

# 第二个返回值只对 static 阶段有意义：它表示当前结果需要改用 stealthy
# 重试。stealthy 本身不会再次触发 fallback，因此会忽略这个值。
FetchJobHandler = Callable[
    ["FetchJob"],
    Awaitable[tuple[FetchSlot, bool]],
]


@dataclass(frozen=True, slots=True)
class FetchJob:
    index: int
    url: str


class FetchBatchScheduler:
    """使用一个共享并发上限调度 static 和 stealthy 抓取。"""

    __slots__ = (
        "_concurrency",
        "_static_job_handler",
        "_stealthy_job_handler",
    )

    def __init__(
        self,
        *,
        concurrency: int,
        static_job_handler: FetchJobHandler,
        stealthy_job_handler: FetchJobHandler,
    ) -> None:
        self._concurrency = max(1, int(concurrency))
        self._static_job_handler = static_job_handler
        self._stealthy_job_handler = stealthy_job_handler

    async def run(
        self,
        urls: list[str],
    ) -> list[FetchSlot]:
        if not urls:
            return []

        results: list[FetchSlot] = [None] * len(urls)
        static_jobs = deque(
            FetchJob(index=index, url=url)
            for index, url in enumerate(urls)
        )
        stealthy_jobs: deque[FetchJob] = deque()
        active: dict[asyncio.Task[object], tuple[FetchJob, bool]] = {}

        try:
            while static_jobs or stealthy_jobs or active:
                while len(active) < self._concurrency and (
                    static_jobs or stealthy_jobs
                ):
                    if static_jobs:
                        job = static_jobs.popleft()
                        task = asyncio.create_task(
                            self._static_job_handler(job),
                        )
                        active[task] = (job, False)
                    else:
                        job = stealthy_jobs.popleft()
                        task = asyncio.create_task(
                            self._stealthy_job_handler(job),
                        )
                        active[task] = (job, True)

                if not active:
                    continue

                completed, _ = await asyncio.wait(
                    active,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in completed:
                    job, is_stealthy = active.pop(task)
                    result, should_fallback = task.result()
                    results[job.index] = result
                    # static 和 stealthy 共用同一个并发池；只有 static 失败时，
                    # 才把原任务放入 stealthy 队列，等待下一个空闲槽位。
                    if not is_stealthy and should_fallback:
                        stealthy_jobs.append(job)
        finally:
            for task in active:
                task.cancel()
            await asyncio.gather(*active, return_exceptions=True)

        return results
