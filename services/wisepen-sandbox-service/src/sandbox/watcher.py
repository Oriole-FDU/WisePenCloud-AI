from __future__ import annotations

import asyncio
from datetime import timedelta

from sandbox.errors import SandboxDomainError
from sandbox.models import SandboxRecord, SandboxSpec, SandboxState, utc_now
from sandbox.ports import SandboxProvider
from sandbox.pool import SandboxPool
from sandbox.repository import InMemorySandboxRepository
from sandbox.scheduler import SandboxScheduler


class Watcher:
    def __init__(
        self,
        pool: SandboxPool,
        repository: InMemorySandboxRepository,
        provider: SandboxProvider,
        spec: SandboxSpec,
        target_ready: int = 2,
        min_ready: int = 1,
        reserve: int = 0,
        max_create_batch: int = 2,
        warmup_timeout_seconds: float = 60,
        destroy_timeout_seconds: float = 60,
        interval_seconds: float = 5,
        max_retries: int = 3,
    ) -> None:
        self._pool = pool
        self._repository = repository
        self._provider = provider
        self._spec = spec
        self._target_ready = target_ready
        self._min_ready = min_ready
        self._reserve = reserve
        self._max_create_batch = max_create_batch
        self._warmup_timeout = warmup_timeout_seconds
        self._destroy_timeout = destroy_timeout_seconds
        self._interval = interval_seconds
        self._max_retries = max_retries
        self._stop = asyncio.Event()
        self._reconcile_lock = asyncio.Lock()
        self._retry_count = 0
        self._warmup_failures = 0
        self._destroy_failures = 0

    async def reconcile(self) -> int:
        async with self._reconcile_lock:
            await self._recover_stale()
            snapshot = await self._pool.snapshot()
            ready = snapshot.counts[SandboxState.READY]
            warming = snapshot.counts[SandboxState.WARMING]
            creating = snapshot.counts[SandboxState.CREATING]
            deficit = max(0, self._target_ready + self._reserve - ready - warming - creating)
            create_count = min(deficit, self._max_create_batch)
            if create_count == 0:
                return 0
            if self._retry_count >= self._max_retries:
                return 0
            created = 0
            for _ in range(create_count):
                try:
                    await self._warm_one()
                except Exception:
                    self._retry_count += 1
                    self._warmup_failures += 1
                    break
                else:
                    self._retry_count = 0
                    created += 1
            return created

    async def _warm_one(self) -> None:
        ref = await self._provider.create(self._spec)
        record = SandboxRecord(ref=ref, state=SandboxState.CREATING)
        await self._repository.save(record)
        try:
            await self._repository.transition(
                ref.sandbox_id,
                SandboxState.CREATING,
                SandboxState.WARMING,
            )
            health = await asyncio.wait_for(
                self._provider.wait_ready(ref, self._warmup_timeout),
                timeout=self._warmup_timeout,
            )
            if not health.healthy:
                raise RuntimeError("sandbox health check failed")
            health_fn = getattr(self._provider, "health", None)
            if health_fn is not None:
                health = await health_fn(ref)
                if not health.healthy:
                    raise RuntimeError("sandbox health check failed")
            await self._pool.add_ready(record)
        except Exception as exc:
            current = await self._repository.get(ref.sandbox_id)
            if current and current.state in (SandboxState.CREATING, SandboxState.WARMING):
                await self._repository.transition(
                    ref.sandbox_id,
                    current.state,
                    SandboxState.DESTROYING,
                    error=str(exc)[:200],
                )
            try:
                await asyncio.wait_for(
                    self._provider.destroy(ref, "warmup_failed"),
                    timeout=self._destroy_timeout,
                )
            finally:
                current = await self._repository.get(ref.sandbox_id)
                if current and current.state == SandboxState.DESTROYING:
                    await self._repository.transition(
                        ref.sandbox_id,
                        SandboxState.DESTROYING,
                        SandboxState.LOST,
                        error=str(exc)[:200],
                    )
            raise

    async def _recover_stale(self) -> None:
        now = utc_now()
        cutoff = now - timedelta(seconds=self._warmup_timeout)
        stale_records = await self._repository.records_older_than(SandboxState.CREATING, cutoff)
        stale_records += await self._repository.records_older_than(SandboxState.WARMING, cutoff)
        for record in stale_records:
            try:
                if record.state == SandboxState.CREATING:
                    await self._repository.transition(
                        record.ref.sandbox_id,
                        SandboxState.CREATING,
                        SandboxState.DESTROYING,
                        error="create timeout",
                    )
                else:
                    await self._repository.transition(
                        record.ref.sandbox_id,
                        SandboxState.WARMING,
                        SandboxState.DESTROYING,
                        error="warmup timeout",
                    )
                await asyncio.wait_for(
                    self._provider.destroy(record.ref, "warmup_timeout"),
                    timeout=self._destroy_timeout,
                )
                await self._repository.transition(
                    record.ref.sandbox_id,
                    SandboxState.DESTROYING,
                    SandboxState.LOST,
                    error="warmup timeout",
                )
            except Exception:
                self._destroy_failures += 1
                continue

        for record in await self._repository.records_older_than(
            SandboxState.DESTROYING,
            now - timedelta(seconds=self._destroy_timeout),
        ):
            try:
                await self._repository.transition(
                    record.ref.sandbox_id,
                    SandboxState.DESTROYING,
                    SandboxState.LOST,
                    error="destroy timeout",
                )
            except SandboxDomainError:
                self._destroy_failures += 1

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.reconcile()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()
