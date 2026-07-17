from __future__ import annotations

import asyncio
from datetime import timedelta
import uuid
from time import monotonic

from common.core.exceptions import ServiceException

from sandbox.domain.entities import SandboxRecord, SandboxSpec, SandboxState, utc_now
from sandbox.domain.interfaces.metrics import MetricsPort
from sandbox.domain.interfaces.leader_lease import LeaderLease
from sandbox.domain.interfaces.sandbox_provider import SandboxProvider
from sandbox.application.services.sandbox_pool import SandboxPool
from sandbox.domain.repositories import SandboxRepository
from sandbox.application.services.sandbox_scheduler import SandboxScheduler


class Watcher:
    def __init__(
        self,
        pool: SandboxPool,
        repository: SandboxRepository,
        provider: SandboxProvider,
        spec: SandboxSpec,
        scheduler: SandboxScheduler | None = None,
        leader_lease: LeaderLease | None = None,
        leader_key: str = "sandbox-watcher",
        target_ready: int = 2,
        min_ready: int = 1,
        reserve: int = 0,
        max_create_batch: int = 2,
        warmup_timeout_seconds: float = 60,
        destroy_timeout_seconds: float = 60,
        interval_seconds: float = 5,
        max_retries: int = 3,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._pool = pool
        self._repository = repository
        self._provider = provider
        self._spec = spec
        self._scheduler = scheduler
        self._leader_lease = leader_lease
        self._leader_key = leader_key
        self._owner = f"watcher-{uuid.uuid4().hex}"
        self._target_ready = max(0, target_ready)
        self._min_ready = max(0, min_ready)
        self._reserve = max(0, reserve)
        self._max_create_batch = max(1, max_create_batch)
        self._warmup_timeout = warmup_timeout_seconds
        self._destroy_timeout = destroy_timeout_seconds
        self._interval = interval_seconds
        self._max_retries = max(1, max_retries)
        self._stop = asyncio.Event()
        self._reconcile_lock = asyncio.Lock()
        self._retry_count = 0
        self._metrics = metrics or repository.metrics

    async def reconcile(self) -> int:
        async with self._reconcile_lock:
            if self._leader_lease and not await self._leader_lease.acquire(
                self._leader_key, self._owner, max(self._interval * 3, 1)
            ):
                self._metrics.increment("watcher_not_leader")
                return 0
            try:
                self._metrics.increment("watcher_reconciles")
                if self._scheduler:
                    await self._scheduler.recover_expired()
                await self._recover_stale()
                snapshot = await self._pool.snapshot()
                ready = snapshot.counts[SandboxState.READY]
                warming = snapshot.counts[SandboxState.WARMING]
                creating = snapshot.counts[SandboxState.CREATING]
                self._metrics.readiness(ready, self._min_ready)
                deficit = max(
                    0,
                    self._target_ready + self._reserve - ready - warming - creating,
                )
                create_count = min(deficit, self._max_create_batch)
                if create_count == 0 or self._retry_count >= self._max_retries:
                    return 0
                created = 0
                for _ in range(create_count):
                    try:
                        await self._warm_one()
                    except Exception:
                        self._retry_count += 1
                        self._metrics.increment("warmup_failures")
                        break
                    else:
                        self._retry_count = 0
                        created += 1
                return created
            finally:
                if self._leader_lease:
                    await self._leader_lease.release(self._leader_key, self._owner)

    async def _warm_one(self) -> None:
        started = monotonic()
        self._metrics.increment("warmup_attempts")
        try:
            ref = await self._provider.create(self._spec)
        except Exception:
            self._metrics.increment("create_failures")
            raise
        record = SandboxRecord(ref=ref, state=SandboxState.CREATING)
        await self._repository.save(record)
        self._metrics.increment("create_successes")
        try:
            await self._repository.transition(
                ref.sandbox_id, SandboxState.CREATING, SandboxState.WARMING
            )
            health = await asyncio.wait_for(
                self._provider.wait_ready(ref, self._warmup_timeout),
                timeout=self._warmup_timeout,
            )
            if not health.healthy:
                raise RuntimeError("sandbox health check failed")
            health_fn = getattr(self._provider, "health", None)
            if health_fn is not None and not (await health_fn(ref)).healthy:
                raise RuntimeError("sandbox health check failed")
            health_token, generation = await self._pool.prepare_readiness(record)
            await self._pool.return_ready(
                record.ref.sandbox_id, health_token, generation
            )
            self._metrics.increment("warmup_successes")
            self._metrics.observe_ms(
                "warmup", (monotonic() - started) * 1000
            )
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
                started = monotonic()
                await asyncio.wait_for(
                    self._provider.destroy(ref, "warmup_failed"),
                    timeout=self._destroy_timeout,
                )
                self._metrics.observe_ms("destroy", (monotonic() - started) * 1000)
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
        stale = await self._repository.records_older_than(SandboxState.CREATING, cutoff)
        stale += await self._repository.records_older_than(SandboxState.WARMING, cutoff)
        for record in stale:
            try:
                await self._repository.transition(
                    record.ref.sandbox_id,
                    record.state,
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
                self._metrics.increment("destroy_failures")
                current = await self._repository.get(record.ref.sandbox_id)
                if current and current.state == SandboxState.DESTROYING:
                    await self._repository.transition(
                        record.ref.sandbox_id,
                        SandboxState.DESTROYING,
                        SandboxState.LOST,
                        error="warmup destroy failed",
                    )

        stale_destroying = await self._repository.records_older_than(
            SandboxState.DESTROYING,
            now - timedelta(seconds=self._destroy_timeout),
        )
        for record in stale_destroying:
            try:
                await self._repository.transition(
                    record.ref.sandbox_id,
                    SandboxState.DESTROYING,
                    SandboxState.LOST,
                    error="destroy timeout",
                )
            except ServiceException:
                self._metrics.increment("destroy_failures")

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.reconcile()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()
