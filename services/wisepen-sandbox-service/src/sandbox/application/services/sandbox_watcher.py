from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
import logging
import uuid
from time import monotonic

from common.core.exceptions import ServiceException
from common.logger import error, info, warn

from sandbox.domain.entities import SandboxRecord, SandboxSpec, SandboxState, utc_now
from sandbox.domain.interfaces.metrics import MetricsPort
from sandbox.domain.interfaces.leader_lease import LeaderLease
from sandbox.domain.interfaces.sandbox_provider import SandboxProvider
from sandbox.application.services.sandbox_pool import SandboxPool
from sandbox.domain.repositories import SandboxRepository
from sandbox.application.services.sandbox_scheduler import SandboxScheduler
logger = logging.getLogger(__name__)


class Watcher:
    """后台预热与恢复循环。

    Watcher 负责维持 READY 池容量、清理预热超时实例，并驱动过期租约回收。
    在多实例部署时可通过 LeaderLease 保证同一时刻只有一个实例执行补池。
    """

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
        warmup_max_retries: int = 3,
        warmup_retry_backoff_seconds: float = 5,
        warmup_retry_max_backoff_seconds: float = 60,
        leader_lease_ttl_seconds: float = 90,
        leader_lease_renew_interval_seconds: float = 20,
        checkpoint_interval_seconds: float = 300,
        idle_rounds_threshold: int = 3,
        idle_interval_seconds: float = 60,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._pool = pool
        self._repository = repository
        self._provider = provider
        self._spec = spec
        self._scheduler = scheduler
        self._lease_manager = getattr(repository, "lease_manager", None)
        self._leader_lease = leader_lease
        self._leader_key = leader_key
        # 持有者标识用于区分多个服务实例的 watcher，内存实现和未来外部锁都依赖它释放租约。
        self._owner = f"watcher-{uuid.uuid4().hex}"
        self._target_ready = max(0, target_ready)
        self._min_ready = max(0, min_ready)
        self._reserve = max(0, reserve)
        self._max_create_batch = max(1, max_create_batch)
        self._warmup_timeout = warmup_timeout_seconds
        self._destroy_timeout = destroy_timeout_seconds
        self._interval = interval_seconds
        # 该值限制指数退避等级，达到上限后保持最大间隔但仍持续尝试恢复。
        self._warmup_max_retries = max(1, warmup_max_retries)
        self._warmup_retry_backoff = warmup_retry_backoff_seconds
        self._warmup_retry_max_backoff = warmup_retry_max_backoff_seconds
        self._leader_lease_ttl = leader_lease_ttl_seconds
        self._leader_lease_renew_interval = leader_lease_renew_interval_seconds
        self._checkpoint_interval = max(1.0, checkpoint_interval_seconds)
        self._next_checkpoint_at = monotonic() + self._checkpoint_interval
        self._idle_rounds = 0
        self._idle_threshold = max(0, idle_rounds_threshold)
        self._idle_interval = max(1.0, idle_interval_seconds)
        self._stop = asyncio.Event()
        self._reconcile_lock = asyncio.Lock()
        self._retry_count = 0
        self._metrics = metrics or repository.metrics

    async def reconcile(self) -> int:
        async with self._reconcile_lock:
            renew_stop: asyncio.Event | None = None
            renew_task: asyncio.Task[None] | None = None
            if self._leader_lease and not await self._leader_lease.acquire(
                self._leader_key, self._owner, self._leader_lease_ttl
            ):
                self._metrics.increment("watcher_not_leader")
                warn(
                    "沙箱 watcher 未获取 leader 租约，跳过本轮补池",
                    owner=self._owner,
                    leader_key=self._leader_key,
                )
                return 0
            if self._leader_lease:
                renew_stop = asyncio.Event()
                renew_task = asyncio.create_task(self._renew_leader_lease(renew_stop))
            try:
                self._metrics.increment("watcher_reconciles")
                if self._scheduler:
                    # 先回收过期用户实例，再评估 READY 缺口，避免旧实例占用资源。
                    await self._scheduler.recover_expired()
                    await self._scheduler.reclaim_idle_users()
                    await self._checkpoint_active_leases()
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
                # 空闲检测：池满时逐渐降低轮询频率以节省资源。
                if deficit > 0:
                    if self._idle_rounds >= self._idle_threshold:
                        info(
                            "沙箱 watcher 检测到补池缺口，退出空闲模式",
                            owner=self._owner,
                            idle_rounds=self._idle_rounds,
                            deficit=deficit,
                        )
                    self._idle_rounds = 0
                else:
                    self._idle_rounds += 1
                info(
                    "沙箱 watcher 开始补池评估",
                    owner=self._owner,
                    ready=ready,
                    warming=warming,
                    creating=creating,
                    target_ready=self._target_ready,
                    min_ready=self._min_ready,
                    reserve=self._reserve,
                    deficit=deficit,
                    create_count=create_count,
                    retry_count=self._retry_count,
                    idle_rounds=self._idle_rounds,
                )
                if create_count == 0:
                    info(
                        "沙箱 watcher 本轮不创建预热实例",
                        owner=self._owner,
                        reason=(
                            "pool_full"
                        ),
                        retry_count=self._retry_count,
                    )
                    return 0
                created = 0
                for attempt in range(1, create_count + 1):
                    try:
                        await self._warm_one(attempt)
                    except Exception as exc:
                        # 连续失败时先停手，避免镜像/运行时异常导致快速创建失败风暴。
                        self._retry_count = min(
                            self._retry_count + 1, self._warmup_max_retries
                        )
                        self._metrics.increment("warmup_failures")
                        error(
                            "沙箱 watcher 预热失败，本轮停止继续创建",
                            exc=exc,
                            owner=self._owner,
                            attempt=attempt,
                            retry_count=self._retry_count,
                        )
                        break
                    else:
                        self._retry_count = 0
                        created += 1
                        info(
                            "沙箱 watcher 预热成功",
                            owner=self._owner,
                            attempt=attempt,
                            created=created,
                        )
                info(
                    "沙箱 watcher 补池完成",
                    owner=self._owner,
                    created=created,
                    retry_count=self._retry_count,
                )
                return created
            finally:
                if renew_stop:
                    renew_stop.set()
                if renew_task:
                    renew_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await renew_task
                if self._leader_lease:
                    await self._leader_lease.release(self._leader_key, self._owner)

    async def _renew_leader_lease(self, stop: asyncio.Event) -> None:
        """续期可覆盖长时间容器预热，避免多副本重复补池。"""
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self._leader_lease_renew_interval
                )
                return
            except asyncio.TimeoutError:
                pass
            assert self._leader_lease is not None
            renewed = await self._leader_lease.acquire(
                self._leader_key, self._owner, self._leader_lease_ttl
            )
            if not renewed:
                error(
                    "沙箱 watcher leader 租约续期失败，将在下个周期重试",
                    owner=self._owner,
                    leader_key=self._leader_key,
                )

    def _next_reconcile_delay(self) -> float:
        if self._retry_count == 0:
            # 连续多轮无需补池时，拉大检查间隔以节省资源。
            if self._idle_rounds >= self._idle_threshold:
                return self._idle_interval
            return self._interval
        # 达到等级上限后维持最大退避但持续重试，防止短暂故障永久耗尽 READY 池。
        exponent = min(self._retry_count, self._warmup_max_retries) - 1
        return min(
            self._warmup_retry_backoff * (2**exponent),
            self._warmup_retry_max_backoff,
        )

    async def _checkpoint_active_leases(self) -> None:
        if not self._scheduler or not self._lease_manager or monotonic() < self._next_checkpoint_at:
            return
        self._next_checkpoint_at = monotonic() + self._checkpoint_interval
        records = await self._repository.records_in([SandboxState.USER_ACTIVE])
        for record in records:
            for lease in await self._lease_manager.active_turns_for_sandbox(record.ref.sandbox_id):
                try:
                    await self._scheduler.checkpoint(
                        lease.lease_id,
                        lease.fencing_token,
                    )
                except Exception as exc:
                    self._metrics.increment("watcher_checkpoint_failures")
                    logger.exception(
                        "sandbox workspace checkpoint failed: sandbox_id=%s lease_id=%s",
                        record.ref.sandbox_id,
                        lease.lease_id,
                        exc_info=exc,
                    )

    async def _warm_one(self, attempt: int | None = None) -> None:
        started = monotonic()
        self._metrics.increment("warmup_attempts")
        info(
            "沙箱预热开始",
            owner=self._owner,
            attempt=attempt,
            image=self._spec.image,
            warmup_timeout_seconds=self._warmup_timeout,
        )
        create_started = monotonic()
        try:
            ref = await self._provider.create(self._spec)
            self._metrics.observe_ms(
                "warmup_create", (monotonic() - create_started) * 1000
            )
        except Exception as exc:
            self._metrics.observe_ms(
                "warmup_create", (monotonic() - create_started) * 1000
            )
            self._metrics.increment("create_failures")
            error(
                "沙箱容器创建失败",
                exc=exc,
                owner=self._owner,
                attempt=attempt,
                image=self._spec.image,
            )
            raise
        info(
            "沙箱容器创建成功",
            owner=self._owner,
            attempt=attempt,
            sandbox_id=ref.sandbox_id,
            provider_id=ref.provider_id,
            endpoint=ref.endpoint.base_url if ref.endpoint else None,
        )
        record = SandboxRecord(ref=ref, state=SandboxState.CREATING)
        await self._repository.save(record)
        self._metrics.increment("create_successes")
        try:
            # 创建状态进入 WARMING 之后才等待健康，防止半创建实例被 checkout。
            info(
                "沙箱预热状态迁移 CREATING -> WARMING",
                sandbox_id=ref.sandbox_id,
            )
            await self._repository.transition(
                ref.sandbox_id, SandboxState.CREATING, SandboxState.WARMING
            )
            info(
                "沙箱开始等待 AIO 就绪",
                sandbox_id=ref.sandbox_id,
                endpoint=ref.endpoint.base_url if ref.endpoint else None,
                timeout_seconds=self._warmup_timeout,
            )
            wait_ready_started = monotonic()
            try:
                health = await asyncio.wait_for(
                    self._provider.wait_ready(ref, self._warmup_timeout),
                    timeout=self._warmup_timeout,
                )
            finally:
                self._metrics.observe_ms(
                    "warmup_wait_ready", (monotonic() - wait_ready_started) * 1000
                )
            self._metrics.increment(
                "warmup_ready_attempts", max(1, health.attempts)
            )
            info(
                "沙箱 AIO 就绪等待结束",
                sandbox_id=ref.sandbox_id,
                healthy=health.healthy,
                status=health.status,
            )
            if not health.healthy:
                raise RuntimeError("沙箱健康检查失败")
            health_fn = getattr(self._provider, "health", None)
            if health_fn is not None:
                info("沙箱开始执行 AIO 就绪复检", sandbox_id=ref.sandbox_id)
                health_check_started = monotonic()
                try:
                    health_check = await health_fn(ref)
                finally:
                    self._metrics.observe_ms(
                        "warmup_ready_check",
                        (monotonic() - health_check_started) * 1000,
                    )
                info(
                    "沙箱 AIO 就绪复检结束",
                    sandbox_id=ref.sandbox_id,
                    healthy=health_check.healthy,
                    status=health_check.status,
                )
                if not health_check.healthy:
                    raise RuntimeError("沙箱健康检查失败")
            # 回到 READY 需要 readiness_token + generation 双重校验，避免并发状态变化后误回池。
            info("沙箱准备进入 READY 池", sandbox_id=ref.sandbox_id)
            health_token, generation = await self._pool.prepare_readiness(record)
            await self._pool.return_ready(
                record.ref.sandbox_id, health_token, generation
            )
            info(
                "沙箱已进入 READY 池",
                sandbox_id=ref.sandbox_id,
                generation=generation,
                warmup_duration_ms=round((monotonic() - started) * 1000, 2),
            )
            self._metrics.increment("warmup_successes")
            self._metrics.observe_ms(
                "warmup", (monotonic() - started) * 1000
            )
        except Exception as exc:
            current = await self._repository.get(ref.sandbox_id)
            error(
                "沙箱预热阶段失败，准备销毁容器",
                exc=exc,
                sandbox_id=ref.sandbox_id,
                provider_id=ref.provider_id,
                endpoint=ref.endpoint.base_url if ref.endpoint else None,
                state=current.state.value if current else None,
            )
            if current and current.state in (SandboxState.CREATING, SandboxState.WARMING):
                info(
                    "沙箱预热失败状态迁移至 DESTROYING",
                    sandbox_id=ref.sandbox_id,
                    previous_state=current.state.value,
                    error_message=str(exc)[:200],
                )
                await self._repository.transition(
                    ref.sandbox_id,
                    current.state,
                    SandboxState.DESTROYING,
                    error=str(exc)[:200],
                )
            destroy_error: Exception | None = None
            try:
                # 预热失败的容器同样要销毁；失败后进入 LOST，便于指标和人工排查。
                started = monotonic()
                info(
                    "开始销毁预热失败的沙箱容器",
                    sandbox_id=ref.sandbox_id,
                    provider_id=ref.provider_id,
                    reason="warmup_failed",
                    timeout_seconds=self._destroy_timeout,
                )
                await asyncio.wait_for(
                    self._provider.destroy(ref, "warmup_failed"),
                    timeout=self._destroy_timeout,
                )
                self._metrics.observe_ms("destroy", (monotonic() - started) * 1000)
                info(
                    "预热失败的沙箱容器销毁完成",
                    sandbox_id=ref.sandbox_id,
                    destroy_duration_ms=round((monotonic() - started) * 1000, 2),
                )
            except Exception as _destroy_exc:
                destroy_error = _destroy_exc
                error(
                    "预热失败的沙箱容器销毁失败",
                    exc=destroy_error,
                    sandbox_id=ref.sandbox_id,
                    provider_id=ref.provider_id,
                )
                # 将原始错误和销毁错误一起抛出，方便上层定位根因。
                raise RuntimeError(
                    f"沙箱销毁失败（原始预热错误: {exc}）"
                ) from destroy_error
            finally:
                current = await self._repository.get(ref.sandbox_id)
                if current and current.state == SandboxState.DESTROYING:
                    if destroy_error is not None:
                        await self._repository.transition(
                            ref.sandbox_id,
                            SandboxState.DESTROYING,
                            SandboxState.LOST,
                            error=str(destroy_error)[:200],
                        )
                        info(
                            "沙箱预热失败后已进入 LOST（销毁失败）",
                            sandbox_id=ref.sandbox_id,
                            destroy_error=str(destroy_error)[:200],
                        )
                    else:
                        await self._repository.transition(
                            ref.sandbox_id,
                            SandboxState.DESTROYING,
                            SandboxState.DESTROYED,
                            error=str(exc)[:200],
                        )
                        info(
                            "沙箱预热失败后已进入 DESTROYED（销毁成功）",
                            sandbox_id=ref.sandbox_id,
                            warmup_error=str(exc)[:200],
                        )
            raise

    async def _recover_stale(self) -> None:
        now = utc_now()
        cutoff = now - timedelta(seconds=self._warmup_timeout)
        # 创建/预热卡住通常意味着 Docker 或 AIO 健康检查异常，直接转 DESTROYING 清理。
        stale = await self._repository.records_older_than(SandboxState.CREATING, cutoff)
        stale += await self._repository.records_older_than(SandboxState.WARMING, cutoff)
        if stale:
            warn(
                "发现超时的沙箱预热实例，开始恢复",
                owner=self._owner,
                count=len(stale),
                cutoff=cutoff.isoformat(),
            )
        for record in stale:
            try:
                info(
                    "开始清理超时的沙箱预热实例",
                    sandbox_id=record.ref.sandbox_id,
                    provider_id=record.ref.provider_id,
                    previous_state=record.state.value,
                )
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
                    SandboxState.DESTROYED,
                    error="warmup timeout",
                )
            except Exception as exc:
                self._metrics.increment("destroy_failures")
                error(
                    "清理超时沙箱预热实例失败",
                    exc=exc,
                    sandbox_id=record.ref.sandbox_id,
                    provider_id=record.ref.provider_id,
                )
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
                # 先尝试再次销毁，避免因临时 Docker 不可用导致资源泄漏。
                await asyncio.wait_for(
                    self._provider.destroy(record.ref, "destroy_timeout_retry"),
                    timeout=self._destroy_timeout,
                )
                await self._repository.transition(
                    record.ref.sandbox_id,
                    SandboxState.DESTROYING,
                    SandboxState.DESTROYED,
                    error="destroy timeout (retry succeeded)",
                )
            except Exception as exc:
                self._metrics.increment("destroy_failures")
                error(
                    "清理 DESTROYING 超时沙箱失败，标记为 LOST",
                    exc=exc,
                    sandbox_id=record.ref.sandbox_id,
                    provider_id=record.ref.provider_id,
                )
                try:
                    await self._repository.transition(
                        record.ref.sandbox_id,
                        SandboxState.DESTROYING,
                        SandboxState.LOST,
                        error="destroy timeout (retry failed)",
                    )
                except ServiceException:
                    self._metrics.increment("destroy_failures")

    async def run(self) -> None:
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

    def wakeup(self) -> None:
        """由外部（例如 checkout 路径）调用，立即退出空闲模式。

        Watcher 在空闲模式下使用较长的轮询间隔。调用此方法可以
        主动唤醒 Watcher，使其在下一轮立即以正常频率评估补池需求。
        """
        self._idle_rounds = 0
