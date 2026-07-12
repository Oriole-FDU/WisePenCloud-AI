from __future__ import annotations

import asyncio
from sandbox.models import SandboxRecord, SandboxSpec, SandboxState, utc_now
from sandbox.ports import SandboxProvider
from sandbox.pool import SandboxPool
from sandbox.repository import InMemorySandboxRepository


class Watcher:
    def __init__(
        self,
        pool: SandboxPool,
        repository: InMemorySandboxRepository,
        provider: SandboxProvider,
        spec: SandboxSpec,
        target_ready: int = 2,
        warmup_timeout_seconds: float = 60,
        interval_seconds: float = 5,
    ) -> None:
        self._pool = pool
        self._repository = repository
        self._provider = provider
        self._spec = spec
        self._target_ready = target_ready
        self._warmup_timeout = warmup_timeout_seconds
        self._interval = interval_seconds
        self._stop = asyncio.Event()
        self._reconcile_lock = asyncio.Lock()

    async def reconcile(self) -> int:
        async with self._reconcile_lock:
            snapshot = await self._pool.snapshot()
            deficit = max(
                0,
                self._target_ready
                - snapshot[SandboxState.READY.value]
                - snapshot[SandboxState.WARMING.value]
                - snapshot[SandboxState.CREATING.value],
            )
            for _ in range(deficit):
                await self._warm_one()
            return deficit

    async def _warm_one(self) -> None:
        record: SandboxRecord | None = None
        try:
            ref = await self._provider.create(self._spec)
            record = SandboxRecord(ref=ref, state=SandboxState.CREATING)
            await self._repository.save(record)
            record.state = SandboxState.WARMING
            record.updated_at = utc_now()
            await self._repository.save(record)
            await self._provider.wait_ready(ref, self._warmup_timeout)
            await self._pool.add_ready(record)
        except Exception:
            if record is None:
                return
            record.state = SandboxState.DESTROYING
            await self._repository.save(record)
            try:
                await self._provider.destroy(record.ref, "warmup_failed")
            finally:
                record.state = SandboxState.LOST
                await self._repository.save(record)

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.reconcile()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()
