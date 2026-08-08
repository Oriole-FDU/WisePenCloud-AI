from __future__ import annotations

import asyncio

from common.logger import error, info

from sandbox_v1.application.services.workspace_service import WorkspaceService


class WorkspaceEvictionWorker:
    """周期执行 Workspace 快照缓存淘汰的后台任务。

    Worker 不直接操作缓存文件或 Repository 状态，只调用 WorkspaceService 的
    evict_snapshots。单轮失败只记录日志，不退出循环，避免临时文件系统异常导致
    后续 TTL/LRU 淘汰永久停止。
    """

    def __init__(
        self,
        *,
        workspace_service: WorkspaceService,
        interval_seconds: float = 3600.0,
    ) -> None:
        self._workspace_service = workspace_service
        self._interval_seconds = max(1.0, interval_seconds)
        self._stopped = asyncio.Event()

    def stop(self) -> None:
        """请求后台淘汰循环停止。"""

        self._stopped.set()

    async def run(self) -> None:
        """按固定间隔执行快照淘汰，直到 stop event 被设置。"""

        self._stopped.clear()
        while not self._stopped.is_set():
            try:
                # 每轮委托 WorkspaceService 执行 TTL 和 LRU 两类淘汰。
                evicted = await self._workspace_service.evict_snapshots()
                if evicted:
                    info("workspace snapshot cache evicted entries", count=len(evicted))
            except Exception as exc:
                # 淘汰失败只影响本轮，下一轮继续尝试。
                error("workspace snapshot cache eviction failed", exc=exc)
            try:
                # 等待 stop 信号；超时表示进入下一轮淘汰。
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self._interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
