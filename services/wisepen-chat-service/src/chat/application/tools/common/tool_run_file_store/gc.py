from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Optional

from common.logger import info, warn
from .store import ToolRunFileStore


class ToolRunFileStoreGcScheduler:
    """ToolRunFileStore 文件系统残留的后台 GC 调度器。

    该类只封装定时执行逻辑，不在模块导入、容器创建或 store 初始化时自动启动。
    应用生命周期后续接入时，需要显式调用 `start()` 和 `stop()`。
    """

    __slots__ = (
        "_initial_delay_seconds",
        "_interval_seconds",
        "_store",
        "_task",
    )

    def __init__(
        self,
        *,
        store: ToolRunFileStore,
        interval_seconds: int = 10 * 60,
        initial_delay_seconds: int = 60,
    ) -> None:
        """初始化 GC 调度器。

        Args:
            store: 需要执行文件系统清理的 ToolRunFileStore 门面。
            interval_seconds: 两次 GC 之间的间隔秒数。
            initial_delay_seconds: 首次启动后的延迟秒数，避免服务启动阶段抢占资源。
        """
        self._store = store
        self._interval_seconds = float(interval_seconds)
        self._initial_delay_seconds = float(initial_delay_seconds)
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        """显式启动后台 GC 循环；重复调用不会创建多个任务。"""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._run_loop(),
            name="tool-run-file-store-gc",
        )
        info(
            "tool run file store gc scheduler started.",
            interval_seconds=int(self._interval_seconds),
            initial_delay_seconds=int(self._initial_delay_seconds),
        )

    async def stop(self) -> None:
        """停止后台 GC 循环，并等待任务退出。"""
        if self._task is None:
            return

        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        info("tool run file store gc scheduler stopped.")

    async def run_once(self) -> None:
        """立即执行一次 GC，用于手工维护、测试或后续管理接口。"""
        await asyncio.to_thread(self._cleanup_once)

    async def _run_loop(self) -> None:
        """循环执行 GC；单次失败只记录日志，不影响下一轮。"""
        if self._initial_delay_seconds:
            await asyncio.sleep(self._initial_delay_seconds)

        while True:
            await self.run_once()
            await asyncio.sleep(self._interval_seconds)

    def _cleanup_once(self) -> None:
        """同步执行一次文件清理，并记录清理结果。"""
        try:
            result = self._store.cleanup_expired_files()
        except Exception as e:
            warn("tool run file store gc failed.", e=e)
            return

        # 只在有实际删除或失败时记录，避免低价值心跳日志刷屏。
        removed = result.removed_objects + result.removed_staging_dirs
        failed = result.failed_objects + result.failed_staging_dirs
        if removed or failed:
            info(
                "tool run file store gc finished.",
                scanned_objects=result.scanned_objects,
                removed_objects=result.removed_objects,
                failed_objects=result.failed_objects,
                scanned_staging_dirs=result.scanned_staging_dirs,
                removed_staging_dirs=result.removed_staging_dirs,
                failed_staging_dirs=result.failed_staging_dirs,
            )
