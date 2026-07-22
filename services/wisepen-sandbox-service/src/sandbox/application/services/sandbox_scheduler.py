from __future__ import annotations

import asyncio
from time import monotonic

from common.core.exceptions import ServiceException

from sandbox.domain.entities import (
    DestroyReason,
    ExecutionRequest,
    ExecutionResult,
    SandboxLease,
    SandboxRecord,
    SandboxRef,
    SandboxState,
    utc_now,
)
from sandbox.domain.interfaces.sandbox_provider import SandboxProvider
from sandbox.domain.interfaces.workspace_store import WorkspaceStore
from sandbox.domain.interfaces.metrics import MetricsPort
from sandbox.application.services.sandbox_pool import SandboxPool
from sandbox.domain.repositories import SandboxRepository
from sandbox.domain.error_codes import SandboxErrorCode


class SandboxScheduler:
    """用户租约生命周期调度器。

    Chat、MCP、VNC 都通过这里租出沙箱、转发执行请求、释放租约并触发销毁。
    """

    def __init__(
        self,
        pool: SandboxPool,
        repository: SandboxRepository,
        provider: SandboxProvider,
        workspace_store: WorkspaceStore,
        destroy_timeout_seconds: float = 30.0,
        destroy_max_retries: int = 3,
        destroy_backoff_seconds: float = 0.1,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._pool = pool
        self._repository = repository
        self._provider = provider
        self._workspace_store = workspace_store
        # 串行化 allocate/execute/release/recover，避免同一租约被并发执行和回收。
        self._lifecycle_lock = asyncio.Lock()
        # 释放可能被 finally、远程桌面空闲清理、过期恢复重复触发，需要本地幂等表。
        self._released_leases: set[str] = set()
        self._destroy_timeout = destroy_timeout_seconds
        self._destroy_max_retries = max(1, destroy_max_retries)
        self._destroy_backoff = destroy_backoff_seconds
        self._metrics = metrics or repository.metrics

    async def allocate(
        self, request_id: str, tenant_id: str, workspace_id: str
    ) -> SandboxLease:
        if not request_id or not tenant_id or not workspace_id:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "请求、租户和工作区不能为空",
            )
        async with self._lifecycle_lock:
            record, lease = await self._pool.checkout(request_id, tenant_id, workspace_id)
            if record.state == SandboxState.RUNNING:
                # 请求标识命中已有租约时直接返回，保证上层重试不会重复激活沙箱。
                return lease
            try:
                # 激活前先恢复同一用户/会话缓存，确保新沙箱继承上次销毁前状态。
                workspace = await self._workspace_store.snapshot(tenant_id, workspace_id)
                await self._provider.prepare_workspace(record.ref, workspace)
                endpoint = await self._provider.activate(record.ref, lease)
                record.ref = SandboxRef(
                    sandbox_id=record.ref.sandbox_id,
                    provider_id=record.ref.provider_id,
                    endpoint=endpoint,
                    metadata=record.ref.metadata,
                )
                await self._repository.save(record)
                await self._repository.transition(
                    record.ref.sandbox_id,
                    SandboxState.ALLOCATED,
                    SandboxState.RUNNING,
                )
                record = await self._repository.get(record.ref.sandbox_id)
                assert record is not None
                return self._lease(record)
            except Exception as exc:
                # 分配失败的实例不能回 READY，必须销毁，避免半恢复工作区继续服务。
                await self._destroy_record(record, DestroyReason.ALLOCATION_FAILED)
                if isinstance(exc, ServiceException):
                    raise
                raise ServiceException(
                    SandboxErrorCode.SANDBOX_UNAVAILABLE,
                    "沙箱分配失败",
                ) from exc

    async def execute(
        self, lease_id: str, request: ExecutionRequest
    ) -> ExecutionResult:
        async with self._lifecycle_lock:
            record = await self._repository.validate_lease(
                lease_id,
                request.tenant_id,
                request.workspace_id,
                request.fencing_token,
            )
            if record.state != SandboxState.RUNNING:
                raise ServiceException(
                    SandboxErrorCode.SANDBOX_UNAVAILABLE,
                    "沙箱租约未运行",
                )
            try:
                return await self._provider.forward(record.ref, request)
            except ServiceException:
                raise
            except Exception as exc:
                raise ServiceException(
                    SandboxErrorCode.SANDBOX_UNAVAILABLE,
                    "沙箱执行失败",
                ) from exc

    async def release(self, lease_id: str, fencing_token: int) -> None:
        async with self._lifecycle_lock:
            if lease_id in self._released_leases:
                return
            record = await self._repository.find_lease(lease_id)
            if record.state in (SandboxState.DESTROYED, SandboxState.LOST):
                self._released_leases.add(lease_id)
                return
            record = await self._repository.close_lease(lease_id, fencing_token)
            if record.state == SandboxState.DESTROYING:
                return
            commit_error: Exception | None = None
            try:
                # 先导出并提交工作区；即使失败，也不能阻止后续 destroy。
                await self._export_and_commit_workspace(record, lease_id)
            except ServiceException as exc:
                commit_error = exc
            finally:
                # 用户实例销毁后由 Watcher 补池，不允许直接复用为 READY。
                await self._destroy_record(record, DestroyReason.LEASE_RELEASED)
                self._released_leases.add(lease_id)
            if commit_error:
                raise commit_error

    async def recover_expired(self) -> int:
        recovered = 0
        async with self._lifecycle_lock:
            for record in await self._repository.expired_leases():
                if not record.lease_id:
                    continue
                lease_id = record.lease_id
                try:
                    # 过期租约同样先关闭入口，再尽力缓存文件，最后销毁实例。
                    await self._repository.close_lease(lease_id, record.fencing_token)
                    try:
                        await self._export_and_commit_workspace(record, lease_id)
                    except ServiceException:
                        # 后台恢复不能因缓存失败卡住销毁；失败详情由指标和异常链路体现。
                        pass
                    await self._destroy_record(record, DestroyReason.LEASE_EXPIRED)
                    self._released_leases.add(lease_id)
                    self._metrics.increment("expired_lease_recoveries")
                except Exception:
                    recovered += 1
                else:
                    recovered += 1
        return recovered

    async def _export_and_commit_workspace(
        self,
        record: SandboxRecord,
        lease_id: str,
    ) -> None:
        try:
            # 沙箱提供者负责从真实沙箱导出完整快照；工作区存储负责持久化缓存。
            snapshot = await self._provider.export_workspace(
                record.ref,
                record.tenant_id or "",
                record.workspace_id or "",
            )
            await self._workspace_store.commit(
                snapshot,
                record.lease_id or lease_id,
                record.fencing_token,
            )
            self._metrics.increment("workspace_commit_successes")
        except Exception as exc:
            # 对上层统一暴露 WORKSPACE_SYNC_FAILED，同时保留原始异常链便于日志定位。
            self._metrics.increment("workspace_commit_failures")
            commit_error = ServiceException(
                SandboxErrorCode.WORKSPACE_SYNC_FAILED,
                "工作区缓存提交失败",
            )
            commit_error.__cause__ = exc
            raise commit_error

    async def status(self, sandbox_id: str) -> SandboxRecord:
        record = await self._repository.get(sandbox_id)
        if record is None:
            raise ServiceException(
                SandboxErrorCode.LEASE_NOT_FOUND,
                f"沙箱 {sandbox_id} 不存在",
            )
        return record

    def _lease(self, record: SandboxRecord) -> SandboxLease:
        return SandboxLease(
            lease_id=record.lease_id or "",
            request_id=record.request_id or "",
            sandbox_id=record.ref.sandbox_id,
            tenant_id=record.tenant_id or "",
            workspace_id=record.workspace_id or "",
            expires_at=record.lease_expires_at or utc_now(),
            fencing_token=record.fencing_token,
            endpoint=record.ref.endpoint,
        )

    async def _destroy_record(
        self, record: SandboxRecord, reason: DestroyReason
    ) -> None:
        if record.state == SandboxState.DESTROYED:
            return
        if record.state != SandboxState.DESTROYING:
            # 销毁前先进入 DESTROYING，阻断新的 execute/return_ready 路径。
            await self._repository.transition(
                record.ref.sandbox_id,
                record.state,
                SandboxState.DESTROYING,
            )
        last_error: Exception | None = None
        for attempt in range(self._destroy_max_retries):
            self._metrics.increment("destroy_attempts")
            started = monotonic()
            try:
                await asyncio.wait_for(
                    self._provider.destroy(record.ref, reason.value),
                    timeout=self._destroy_timeout,
                )
                self._metrics.observe_ms(
                    "destroy", (monotonic() - started) * 1000
                )
                self._metrics.increment("destroy_successes")
                last_error = None
                break
            except Exception as exc:
                self._metrics.observe_ms(
                    "destroy", (monotonic() - started) * 1000
                )
                last_error = exc
                if attempt + 1 < self._destroy_max_retries:
                    # 容器或 AIO 偶发失败时短暂退避重试，避免瞬时错误直接标 LOST。
                    await asyncio.sleep(self._destroy_backoff * (2**attempt))
        if last_error is not None:
            # 销毁连续失败的实例不可再被分配，进入 LOST 等待人工或外部清理。
            await self._repository.transition(
                record.ref.sandbox_id,
                SandboxState.DESTROYING,
                SandboxState.LOST,
                error=str(last_error)[:200],
            )
            self._metrics.increment("destroy_failures")
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "沙箱销毁失败",
            ) from last_error
        await self._repository.transition(
            record.ref.sandbox_id,
            SandboxState.DESTROYING,
            SandboxState.DESTROYED,
        )
        await self._repository.clear_lease(record)
