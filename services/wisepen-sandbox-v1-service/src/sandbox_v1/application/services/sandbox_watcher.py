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
    """后台容器池维护器。

    Watcher 负责补足 READY 容器、执行 warmup 发布、清理 warmup 失败容器，
    并回收长时间停留在生命周期中间态的 stale 记录。它不处理用户 workspace
    行为，也不决定用户绑定，只执行 Pool 给出的维护计划。
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
        # 依赖端口与创建规格：Pool 负责计划，Repository 负责状态机，Provider 负责运行时。
        self._pool = pool
        self._repository = repository
        self._provider = provider
        self._spec = spec

        # 补池策略参数：目标水位由 Pool 快照给出，reserve/batch 控制额外余量和单轮动作量。
        self._min_ready = max(0, min_ready)
        self._reserve = max(0, reserve)
        self._max_create_batch = max(1, max_create_batch)

        # 生命周期超时与循环间隔：warmup/destroy 分别保护创建和销毁路径。
        self._warmup_timeout = warmup_timeout_seconds
        self._destroy_timeout = destroy_timeout_seconds
        self._interval = max(0.1, interval_seconds)

        # warmup 失败后的指数退避参数。
        self._warmup_max_retries = max(1, warmup_max_retries)
        self._retry_backoff = max(0.1, warmup_retry_backoff_seconds)
        self._retry_max_backoff = max(self._retry_backoff, warmup_retry_max_backoff_seconds)
        self._retry_count = 0

        # 运行态控制：stop 结束循环，lock 防止并发 reconcile，metrics 记录观测数据。
        self._stop = asyncio.Event()
        self._reconcile_lock = asyncio.Lock()
        self._metrics = metrics or repository.metrics

    async def reconcile(self) -> int:
        """执行一次串行维护周期，并返回成功发布 READY 的容器数量。"""

        # 同一时刻只允许一个维护周期，避免重复补池或并发销毁同一条记录。
        async with self._reconcile_lock:
            self._metrics.increment("watcher_reconciles")

            # 每轮补池前先恢复 stuck 的生命周期记录，避免旧中间态长期占用供给。
            await self._recover_stale()

            # 计算本轮容量计划，并上报当前 READY 水位。
            plan = await self._pool.maintenance_plan(
                reserve=self._reserve,
                max_create_batch=self._max_create_batch,
            )
            self._metrics.readiness(plan.ready, self._min_ready)

            # 没有缺口时本轮无需创建。
            if not plan.create_count > 0:
                return 0

            # 按计划逐个创建并预热；任一失败会停止本轮后续创建，交给退避控制下一轮。
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

                # 成功发布一个 READY 后，清空连续失败计数。
                self._retry_count = 0
                created += 1
            return created

    def _next_reconcile_delay(self) -> float:
        """根据最近 warmup 失败次数决定下一轮维护等待时间。"""

        # 没有连续失败时使用固定巡检间隔。
        if self._retry_count == 0:
            return self._interval

        # 有失败时使用指数退避，并受最大 backoff 限制。
        exponent = min(self._retry_count, self._warmup_max_retries) - 1
        return min(self._retry_backoff * (2**exponent), self._retry_max_backoff)

    async def _warm_one(self, attempt: int | None = None) -> None:
        """创建、预热、复检并发布一个 READY 容器。"""

        # 记录 warmup 开始时间，并创建 provider 层真实容器。
        started = monotonic()
        self._metrics.increment("warmup_attempts")
        ref = await self._provider.create(self._spec)

        # 保存 CREATING 记录，使后续失败或进程重启都能从 Repository 观测到。
        record = SandboxRecord(ref=ref, state=SandboxState.CREATING)
        await self._repository.save(record)
        self._metrics.increment("create_successes")

        try:
            # 进入 WARMING 后等待 provider ready；timeout 保护预热路径不会无限卡住。
            await self._repository.transition(ref.sandbox_id, SandboxState.CREATING, SandboxState.WARMING)
            health = await asyncio.wait_for(
                self._provider.wait_ready(ref, self._warmup_timeout),
                timeout=self._warmup_timeout,
            )
            self._metrics.increment("warmup_ready_attempts", max(1, health.attempts))

            # wait_ready 的结果是第一次健康判断，失败则进入销毁补偿。
            if not health.healthy:
                raise RuntimeError(f"container health check failed: {health.status}")

            # 发布 READY 前再复检一次，避免刚 ready 后立刻退化的容器进入池。
            health_check = await self._provider.health(ref)
            if not health_check.healthy:
                raise RuntimeError(f"container health recheck failed: {health_check.status}")

            # 复检通过后才发布 READY，并记录 warmup 成功指标。
            await self._repository.transition(ref.sandbox_id, SandboxState.WARMING, SandboxState.READY)
            self._metrics.increment("ready_publishes")
            self._metrics.increment("warmup_successes")
            self._metrics.observe_ms("warmup", (monotonic() - started) * 1000)
            info("sandbox container entered READY", sandbox_id=ref.sandbox_id, attempt=attempt)
        except Exception as exc:
            # 任意 warmup 异常都先清理容器，再把错误交回 reconcile 做退避处理。
            await self._destroy_failed_warmup(record, exc)
            raise

    async def _destroy_failed_warmup(
        self, record: SandboxRecord, warmup_error: Exception
    ) -> None:
        """清理 warmup 失败的容器，并尽量把记录落到 DESTROYED 或 LOST。"""

        # 如果记录仍在创建/预热中，先推进到 DESTROYING，留下可观测的清理状态。
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
            # 尝试在 destroy timeout 内销毁 provider 容器。
            self._metrics.increment("destroy_attempts")
            await asyncio.wait_for(self._provider.destroy(record.ref, "warmup_failed"), timeout=self._destroy_timeout)
        except Exception as exc:
            destroy_error = exc
            self._metrics.increment("destroy_failures")
        finally:
            # 重新读取当前记录，避免用 warmup 开始时的旧状态做最终转换。
            current = await self._repository.get(record.ref.sandbox_id)
            if current and current.state == SandboxState.DESTROYING:
                # destroy 成功转 DESTROYED，失败转 LOST 并保留失败原因。
                await self._repository.transition(
                    record.ref.sandbox_id,
                    SandboxState.DESTROYING,
                    SandboxState.LOST if destroy_error else SandboxState.DESTROYED,
                    error=str(destroy_error or warmup_error)[:200],
                )

        if destroy_error:
            # 销毁失败需要向上抛出，让 reconcile 记录失败并进入退避。
            raise RuntimeError("failed to destroy an unhealthy container") from destroy_error

    async def _recover_stale(self) -> None:
        """查找超时停留在中间态的记录，并触发 best-effort 清理。"""

        now = utc_now()

        # 收集超过 warmup timeout 的 CREATING/WARMING 记录。
        warmup_cutoff = now - timedelta(seconds=self._warmup_timeout)
        stale = await self._repository.records_older_than(
            SandboxState.CREATING, warmup_cutoff
        )
        stale += await self._repository.records_older_than(
            SandboxState.WARMING, warmup_cutoff
        )

        # 预热超时的记录按 warmup_timeout 原因清理。
        for record in stale:
            await self._destroy_stale(record, "warmup_timeout")

        # 长时间停在 DESTROYING 的记录重试销毁。
        destroy_cutoff = now - timedelta(seconds=self._destroy_timeout)
        for record in await self._repository.records_older_than(
            SandboxState.DESTROYING, destroy_cutoff
        ):
            await self._destroy_stale(record, "destroy_timeout_retry")

    async def _destroy_stale(self, record: SandboxRecord, reason: str) -> None:
        """Best-effort 清理 stale 容器，失败时用 LOST 保留失败证据。"""

        try:
            # 非 DESTROYING 记录先推进到 DESTROYING，统一后续销毁状态。
            if record.state != SandboxState.DESTROYING:
                await self._repository.transition(
                    record.ref.sandbox_id,
                    record.state,
                    SandboxState.DESTROYING,
                    error=reason,
                )

            # 执行 provider destroy，并用 destroy timeout 防止清理路径卡住。
            self._metrics.increment("destroy_attempts")
            await asyncio.wait_for(
                self._provider.destroy(record.ref, reason),
                timeout=self._destroy_timeout,
            )

            # 销毁成功后重新读取当前状态，再从 DESTROYING 转 DESTROYED。
            current = await self._repository.get(record.ref.sandbox_id)
            if current and current.state == SandboxState.DESTROYING:
                await self._repository.transition(
                    record.ref.sandbox_id,
                    SandboxState.DESTROYING,
                    SandboxState.DESTROYED,
                    error=reason,
                )
        except (Exception, ServiceException) as exc:
            # 清理失败只记录并尽量转 LOST，保持 stale 回收是 best-effort。
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
                    # 如果仍在 DESTROYING，转 LOST 保存失败证据；二次失败则吞掉。
                    await self._repository.transition(
                        record.ref.sandbox_id,
                        SandboxState.DESTROYING,
                        SandboxState.LOST,
                        error=str(exc)[:200],
                    )
                except ServiceException:
                    pass

    async def run(self) -> None:
        """按 delay 循环执行 reconcile，直到 stop event 被设置。"""

        while not self._stop.is_set():
            # 每轮先执行一次维护周期。
            await self.reconcile()
            try:
                # 等待 stop 信号；超时表示进入下一轮维护。
                await asyncio.wait_for(self._stop.wait(), timeout=self._next_reconcile_delay())
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """请求 watcher 循环停止。"""

        # 只设置停止信号，不等待 run 协程退出。
        self._stop.set()
