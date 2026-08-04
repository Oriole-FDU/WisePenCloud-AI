from __future__ import annotations

import asyncio
from datetime import timedelta
from time import monotonic

from common.core.exceptions import ServiceException
from common.logger import error, info

from sandbox_v1.application.services.sandbox_pool import SandboxPool
from sandbox_v1.domain.entities import SandboxRecord, SandboxSpec, SandboxState, utc_now
from sandbox_v1.domain.interfaces.metrics import MetricsPort
from sandbox_v1.domain.interfaces.sandbox_provider import SandboxProvider
from sandbox_v1.domain.repositories import SandboxRepository


class Watcher:
    """Maintain, replenish, and clean up the container pool.

    The watcher owns no user workspace behavior. It only executes the pool
    maintenance plan and keeps failed lifecycle transitions from leaking
    containers.
    """

    def __init__(
        self,
        pool: SandboxPool,
        repository: SandboxRepository,
        provider: SandboxProvider,
        spec: SandboxSpec,
        *,
        min_ready: int = 1,
        reserve: int = 0,
        max_create_batch: int = 2,
        warmup_timeout_seconds: float = 60,
        destroy_timeout_seconds: float = 60,
        interval_seconds: float = 5,
        warmup_max_retries: int = 3,
        warmup_retry_backoff_seconds: float = 5,
        warmup_retry_max_backoff_seconds: float = 60,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._pool = pool
        self._repository = repository
        self._provider = provider
        self._spec = spec
        self._min_ready = max(0, min_ready)
        self._reserve = max(0, reserve)
        self._max_create_batch = max(1, max_create_batch)
        self._warmup_timeout = warmup_timeout_seconds
        self._destroy_timeout = destroy_timeout_seconds
        self._interval = max(0.1, interval_seconds)
        self._warmup_max_retries = max(1, warmup_max_retries)
        self._retry_backoff = max(0.1, warmup_retry_backoff_seconds)
        self._retry_max_backoff = max(self._retry_backoff, warmup_retry_max_backoff_seconds)
        self._retry_count = 0
        self._stop = asyncio.Event()
        self._reconcile_lock = asyncio.Lock()
        self._metrics = metrics or repository.metrics

    async def reconcile(self) -> int:
        """Run one serialized maintenance pass."""
        async with self._reconcile_lock:
            self._metrics.increment("watcher_reconciles")
            await self._recover_stale()
            plan = await self._pool.maintenance_plan(
                reserve=self._reserve,
                max_create_batch=self._max_create_batch,
            )
            self._metrics.readiness(plan.ready, self._min_ready)

            if not plan.should_replenish:
                return 0

            created = 0
            for attempt in range(1, plan.create_count + 1):
                try:
                    await self._warm_one(attempt)
                except Exception as exc:
                    self._retry_count = min(
                        self._retry_count + 1,
                        self._warmup_max_retries,
                    )
                    self._metrics.increment("warmup_failures")
                    error(
                        "sandbox pool replenishment failed",
                        exc=exc,
                        attempt=attempt,
                        retry_count=self._retry_count,
                    )
                    break
                self._retry_count = 0
                created += 1
            return created

    def _next_reconcile_delay(self) -> float:
        if self._retry_count == 0:
            return self._interval
        exponent = min(self._retry_count, self._warmup_max_retries) - 1
        return min(self._retry_backoff * (2**exponent), self._retry_max_backoff)

    async def _warm_one(self, attempt: int | None = None) -> None:
        """Create, warm, validate, and publish one READY container."""
        started = monotonic()
        self._metrics.increment("warmup_attempts")
        ref = await self._provider.create(self._spec)
        record = SandboxRecord(ref=ref, state=SandboxState.CREATING)
        await self._repository.save(record)
        self._metrics.increment("create_successes")

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
            self._metrics.increment("warmup_ready_attempts", max(1, health.attempts))
            if not health.healthy:
                raise RuntimeError(f"container health check failed: {health.status}")

            health_check = await self._provider.health(ref)
            if not health_check.healthy:
                raise RuntimeError(f"container health recheck failed: {health_check.status}")

            await self._repository.transition(
                ref.sandbox_id,
                SandboxState.WARMING,
                SandboxState.READY,
            )
            self._metrics.increment("ready_publishes")
            self._metrics.increment("warmup_successes")
            self._metrics.observe_ms("warmup", (monotonic() - started) * 1000)
            info(
                "sandbox container entered READY",
                sandbox_id=ref.sandbox_id,
                attempt=attempt,
            )
        except Exception as exc:
            await self._destroy_failed_warmup(record, exc)
            raise

    async def _destroy_failed_warmup(
        self, record: SandboxRecord, warmup_error: Exception
    ) -> None:
        """Move failed warmups through DESTROYING before removing the container."""
        current = await self._repository.get(record.ref.sandbox_id)
        if current and current.state in (SandboxState.CREATING, SandboxState.WARMING):
            await self._repository.transition(
                record.ref.sandbox_id,
                current.state,
                SandboxState.DESTROYING,
                error=str(warmup_error)[:200],
            )

        destroy_error: Exception | None = None
        try:
            self._metrics.increment("destroy_attempts")
            await asyncio.wait_for(
                self._provider.destroy(record.ref, "warmup_failed"),
                timeout=self._destroy_timeout,
            )
        except Exception as exc:
            destroy_error = exc
            self._metrics.increment("destroy_failures")
        finally:
            current = await self._repository.get(record.ref.sandbox_id)
            if current and current.state == SandboxState.DESTROYING:
                await self._repository.transition(
                    record.ref.sandbox_id,
                    SandboxState.DESTROYING,
                    SandboxState.LOST if destroy_error else SandboxState.DESTROYED,
                    error=str(destroy_error or warmup_error)[:200],
                )

        if destroy_error:
            raise RuntimeError("failed to destroy an unhealthy container") from destroy_error

    async def _recover_stale(self) -> None:
        """Retry cleanup for containers stuck in lifecycle transition states."""
        now = utc_now()
        warmup_cutoff = now - timedelta(seconds=self._warmup_timeout)
        stale = await self._repository.records_older_than(
            SandboxState.CREATING, warmup_cutoff
        )
        stale += await self._repository.records_older_than(
            SandboxState.WARMING, warmup_cutoff
        )

        for record in stale:
            await self._destroy_stale(record, "warmup_timeout")

        destroy_cutoff = now - timedelta(seconds=self._destroy_timeout)
        for record in await self._repository.records_older_than(
            SandboxState.DESTROYING, destroy_cutoff
        ):
            await self._destroy_stale(record, "destroy_timeout_retry")

    async def _destroy_stale(self, record: SandboxRecord, reason: str) -> None:
        """Best-effort stale cleanup; LOST preserves evidence of failure."""
        try:
            if record.state != SandboxState.DESTROYING:
                await self._repository.transition(
                    record.ref.sandbox_id,
                    record.state,
                    SandboxState.DESTROYING,
                    error=reason,
                )
            self._metrics.increment("destroy_attempts")
            await asyncio.wait_for(
                self._provider.destroy(record.ref, reason),
                timeout=self._destroy_timeout,
            )
            current = await self._repository.get(record.ref.sandbox_id)
            if current and current.state == SandboxState.DESTROYING:
                await self._repository.transition(
                    record.ref.sandbox_id,
                    SandboxState.DESTROYING,
                    SandboxState.DESTROYED,
                    error=reason,
                )
        except (Exception, ServiceException) as exc:
            self._metrics.increment("destroy_failures")
            error(
                "sandbox stale container cleanup failed",
                exc=exc,
                sandbox_id=record.ref.sandbox_id,
                reason=reason,
            )
            current = await self._repository.get(record.ref.sandbox_id)
            if current and current.state == SandboxState.DESTROYING:
                try:
                    await self._repository.transition(
                        record.ref.sandbox_id,
                        SandboxState.DESTROYING,
                        SandboxState.LOST,
                        error=str(exc)[:200],
                    )
                except ServiceException:
                    pass

    async def run(self) -> None:
        """Run maintenance until shutdown is requested."""
        while not self._stop.is_set():
            await self.reconcile()
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._next_reconcile_delay()
                )
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()
