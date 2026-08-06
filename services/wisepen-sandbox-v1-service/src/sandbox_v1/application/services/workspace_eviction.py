from __future__ import annotations

import asyncio

from common.logger import error, info

from sandbox_v1.application.services.workspace_service import WorkspaceService


class WorkspaceEvictionWorker:
    """Periodic TTL/LRU enforcement for host-side snapshot cache."""

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
        self._stopped.set()

    async def run(self) -> None:
        self._stopped.clear()
        while not self._stopped.is_set():
            try:
                evicted = await self._workspace_service.evict_snapshots()
                if evicted:
                    info("workspace snapshot cache evicted entries", count=len(evicted))
            except Exception as exc:
                error("workspace snapshot cache eviction failed", exc=exc)
            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self._interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
