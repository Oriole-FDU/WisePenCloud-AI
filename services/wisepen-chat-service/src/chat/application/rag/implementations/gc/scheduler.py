import asyncio
from typing import Optional, Set

from common.logger import log_fail, log_ok
from .service import RagIndexGcService


class RagIndexGcScheduler:
    """RAG index GC 定时任务。

    - 周期执行 sweep_unpublished_versions。
    - 扫描源是 Manifest 表。
    - 不删除 Manifest 当前线上版本。
    - 失败只记录日志，不影响主服务。
    """

    def __init__(
            self,
            gc_service: RagIndexGcService,
            interval_seconds: int = 24 * 60 * 60,
            sweep_limit: int = 100,
            initial_delay_seconds: int = 60,
    ) -> None:
        self._gc_service = gc_service
        self._interval_seconds = interval_seconds
        self._sweep_limit = sweep_limit
        self._initial_delay_seconds = initial_delay_seconds

        self._task: Optional[asyncio.Task[None]] = None
        # 声明一个强引用集合，防范 Task 运行中被 GC 意外回收
        self._background_tasks: Set[asyncio.Task[None]] = set()

    def start(self) -> None:
        # 防止重复启动
        if self._task is not None and not self._task.done():
            return

        task = asyncio.create_task(self._run_loop())

        # 强绑定生命周期
        self._task = task
        self._background_tasks.add(task)
        # 任务结束时自动从强引用集合移除，避免内存泄漏
        task.add_done_callback(self._background_tasks.discard)

    async def close(self) -> None:
        if self._task is None:
            return

        self._task.cancel()

        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run_loop(self) -> None:
        # 启动后等待初始延迟，避免服务刚启动时产生 GC 压力
        if self._initial_delay_seconds > 0:
            await asyncio.sleep(self._initial_delay_seconds)

        while True:
            try:
                results = await self._gc_service.sweep_unpublished_versions(
                    limit=self._sweep_limit,
                )

                cleaned_count = sum(
                    len(result.cleaned_index_versions)
                    for result in results
                )

                log_ok(
                    "rag_index_gc_scheduler",
                    resource_count=len(results),
                    cleaned_index_version_count=cleaned_count,
                )
            except asyncio.CancelledError:
                # 不能把系统的 CancelledError 当作普通业务 Exception 吞掉，
                # 必须向上抛出以响应系统的 shutdown 信号
                raise
            except Exception as e:
                log_fail(
                    "rag_index_gc_scheduler",
                    f"RAG index GC sweep failed: {repr(e)}",
                )

            await asyncio.sleep(self._interval_seconds)