from __future__ import annotations

import asyncio
from dataclasses import replace
from time import monotonic

from common.core.exceptions import ServiceException
from sandbox.application.services.sandbox_pool import SandboxPool
from sandbox.domain.entities import (
    DestroyReason,
    ExecutionRequest,
    ExecutionResult,
    SandboxLease,
    SandboxRecord,
    SandboxRef,
    SandboxState,
    UserSandboxBindingRecord,
)
from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.domain.interfaces.metrics import MetricsPort
from sandbox.domain.interfaces.sandbox_provider import SandboxProvider
from sandbox.domain.interfaces.workspace_store import WorkspaceStore
from sandbox.domain.repositories import SandboxRepository
from sandbox.domain.repositories.binding_manager import BindingManager
from sandbox.domain.repositories.lease_manager import LeaseManager
from sandbox.domain.repositories.workspace_manager import WorkspaceManager

_MUTATING_OPERATIONS = {"write_file", "edit_file", "shell_exec", "execute"}


class SandboxScheduler:
    """Own one reusable container per user and one fenced lease per Chat turn."""

    def __init__(
        self,
        pool: SandboxPool,
        repository: SandboxRepository,
        provider: SandboxProvider,
        workspace_store: WorkspaceStore,
        destroy_timeout_seconds: float = 30.0,
        destroy_max_retries: int = 3,
        destroy_backoff_seconds: float = 0.1,
        user_reuse_enabled: bool = True,
        user_idle_ttl_seconds: int = 600,
        max_user_bindings: int = 20,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._pool = pool
        self._repository = repository
        self._provider = provider
        self._workspace_store = workspace_store
        self._lease_manager: LeaseManager = getattr(repository, "lease_manager", repository)
        self._binding_manager: BindingManager = getattr(repository, "binding_manager", repository)
        self._workspace_manager: WorkspaceManager = getattr(repository, "workspace_manager", repository)

        self._capacity_lock = asyncio.Lock()
        self._user_creation_locks: dict[str, asyncio.Lock] = {}
        self._workspace_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._destroy_lock = asyncio.Lock()

        self._released_leases: set[str] = set()

        self._destroy_timeout = destroy_timeout_seconds
        self._destroy_max_retries = max(1, destroy_max_retries)
        self._destroy_backoff = destroy_backoff_seconds
        self._user_reuse_enabled = user_reuse_enabled
        self._user_idle_ttl = user_idle_ttl_seconds
        self._max_user_bindings = max_user_bindings
        self._metrics = metrics or repository.metrics

    async def allocate(self, request_id: str, tenant_id: str, workspace_id: str) -> SandboxLease:
        if not request_id or not tenant_id or not workspace_id:
            raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE, "请求、用户和 session 不能为空")

        binding = await self._binding_manager.find_user_binding(tenant_id)
        if binding is None:
            creation_lock = self._user_creation_locks.setdefault(tenant_id, asyncio.Lock())
            async with creation_lock:
                return await self._allocate_after_capacity_check(request_id, tenant_id, workspace_id)
        return await self._allocate_bound_user(request_id, tenant_id, workspace_id)

    async def _allocate_after_capacity_check(
        self, request_id: str, user_id: str, session_id: str
    ) -> SandboxLease:
        if await self._binding_manager.find_user_binding(user_id) is None:
            async with self._capacity_lock:
                await self._evict_lru_for_new_user(user_id)
                return await self._allocate_bound_user(request_id, user_id, session_id)
        return await self._allocate_bound_user(request_id, user_id, session_id)

    async def _allocate_bound_user(
        self, request_id: str, user_id: str, session_id: str
    ) -> SandboxLease:
        existing = await self._lease_manager.find_turn_request(request_id)
        record, lease = await self._pool.checkout(request_id, user_id, session_id)
        # Idempotent retries must not restore or reactivate the workspace again.
        if existing is not None:
            return lease
        try:
            if not lease.workspace_reused:
                workspace = await self._workspace_store.snapshot(user_id, session_id)
                await self._provider.prepare_workspace(record.ref, workspace)
                await self._workspace_manager.mark_workspace_prepared(user_id, session_id)
                self._metrics.increment("workspace_restore_misses")
            else:
                self._metrics.increment("workspace_restore_hits")

            endpoint = record.ref.endpoint
            if record.state == SandboxState.ALLOCATED:
                endpoint = await self._provider.activate(record.ref, lease)
                record.ref = SandboxRef(
                    sandbox_id=record.ref.sandbox_id,
                    provider_id=record.ref.provider_id,
                    endpoint=endpoint,
                    metadata=record.ref.metadata,
                )
                await self._repository.save(record)
                await self._binding_manager.activate_user_binding(record.ref.sandbox_id)
            return replace(lease, endpoint=endpoint)
        except Exception as exc:
            if record.state == SandboxState.ALLOCATED:
                await self._destroy_record(record, DestroyReason.ALLOCATION_FAILED)
            else:
                await self._abort_lease(lease, exc)
                await self._workspace_manager.remove_workspace(user_id, session_id)
            if isinstance(exc, ServiceException):
                raise
            raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE, "沙箱分配失败") from exc

    async def _evict_lru_for_new_user(self, user_id: str) -> None:
        if await self._binding_manager.find_user_binding(user_id) is not None:
            return
        bindings = await self._binding_manager.user_bindings()
        if len(bindings) < self._max_user_bindings:
            return
        idle = await self._binding_manager.idle_user_bindings()
        if not idle:
            raise ServiceException(SandboxErrorCode.USER_SANDBOX_CAPACITY, "没有可淘汰的 USER_IDLE 容器")
        await self._retire_binding(idle[0], DestroyReason.USER_LRU_EVICTED)
        self._metrics.increment("user_lru_reclaims")

    async def execute(self, lease_id: str, request: ExecutionRequest) -> ExecutionResult:
        record = await self._lease_manager.validate_lease(
            lease_id,
            request.tenant_id,
            request.workspace_id,
            request.fencing_token,
        )
        if request.operation in _MUTATING_OPERATIONS:
            await self._workspace_manager.mark_workspace_dirty(request.tenant_id, request.workspace_id)
        try:
            # Never hold a lifecycle lock while AIO executes; other session folders share this container.
            workspace_lock = self._workspace_locks.setdefault(
                (request.tenant_id, request.workspace_id), asyncio.Lock()
            )
            async with workspace_lock:
                return await self._provider.forward(record.ref, request)
        except ServiceException:
            raise
        except asyncio.TimeoutError as exc:
            self._metrics.increment("execution_timeouts")
            raise ServiceException(SandboxErrorCode.EXECUTION_TIMEOUT, "沙箱任务执行超时") from exc
        except Exception as exc:
            raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE, "沙箱执行失败") from exc

    async def checkpoint(self, lease_id: str, fencing_token: int) -> None:
        lease = await self._lease_manager.get_turn_lease(lease_id)
        record = await self._lease_manager.validate_lease(
            lease_id, lease.tenant_id, lease.workspace_id, fencing_token
        )
        await self._checkpoint_workspace(record, lease_id, lease.fencing_token, lease.tenant_id, lease.workspace_id)

    async def release(
        self,
        lease_id: str,
        fencing_token: int,
        *,
        retire_when_reuse_disabled: bool = True,
    ) -> None:
        if lease_id in self._released_leases:
            return
        lease = await self._lease_manager.get_turn_lease(lease_id)
        if lease.released_at is not None:
            self._released_leases.add(lease_id)
            return
        record = await self._lease_manager.close_lease(lease_id, fencing_token)
        checkpoint_error: Exception | None = None
        try:
            await self._checkpoint_workspace(
                record, lease_id, fencing_token, lease.tenant_id, lease.workspace_id
            )
        except ServiceException as exc:
            checkpoint_error = exc
            self._metrics.increment("workspace_checkpoint_degraded")
        record = await self._lease_manager.finish_release(
            lease_id,
            self._user_idle_ttl,
            error=str(checkpoint_error)[:200] if checkpoint_error else None,
        )
        self._released_leases.add(lease_id)
        if (
            retire_when_reuse_disabled
            and not self._user_reuse_enabled
            and record.active_turn_count == 0
        ):
            binding = await self._binding_manager.binding_for_sandbox(record.ref.sandbox_id)
            if binding:
                await self._retire_binding(binding, DestroyReason.LEASE_RELEASED)

    async def _abort_lease(self, lease: SandboxLease, exc: Exception) -> None:
        try:
            await self._lease_manager.close_lease(lease.lease_id, lease.fencing_token)
            await self._lease_manager.finish_release(
                lease.lease_id, self._user_idle_ttl, error=str(exc)[:200]
            )
        except ServiceException:
            pass

    async def release_request(self, request_id: str, tenant_id: str, workspace_id: str) -> None:
        lease = await self._lease_manager.find_turn_request(request_id)
        if lease is None:
            return
        if lease.tenant_id != tenant_id or lease.workspace_id != workspace_id:
            raise ServiceException(SandboxErrorCode.FENCING_REJECTED, "租约上下文不匹配")
        await self.release(lease.lease_id, lease.fencing_token)

    async def release_session_turn(self, tenant_id: str, workspace_id: str) -> None:
        lease = await self._lease_manager.active_turn_for_session(tenant_id, workspace_id)
        if lease is not None:
            await self.release(lease.lease_id, lease.fencing_token)

    async def delete_workspace(self, user_id: str, session_id: str) -> bool:
        workspace = await self._workspace_manager.find_workspace(user_id, session_id)
        binding = await self._binding_manager.find_user_binding(user_id)
        if workspace is None and binding is None:
            await self._workspace_store.delete(user_id, session_id)
            return False
        lease = await self._lease_manager.active_turn_for_session(user_id, session_id)
        if lease is not None:
            await self._lease_manager.close_lease(lease.lease_id, lease.fencing_token)
            await self._lease_manager.finish_release(
                lease.lease_id, self._user_idle_ttl, error="session workspace deleted"
            )
            self._released_leases.add(lease.lease_id)
        if binding is not None:
            record = await self._repository.get(binding.sandbox_id)
            if record is not None:
                workspace_lock = self._workspace_locks.setdefault((user_id, session_id), asyncio.Lock())
                async with workspace_lock:
                    await self._provider.delete_workspace(record.ref, user_id, session_id)
        await self._workspace_store.delete(user_id, session_id)
        await self._workspace_manager.remove_workspace(user_id, session_id)
        self._metrics.increment("session_workspace_deletes")
        return True

    # Compatibility alias for callers upgraded in a separate deployment step.
    async def destroy_session(self, tenant_id: str, workspace_id: str) -> bool:
        return await self.delete_workspace(tenant_id, workspace_id)

    async def destroy_user(self, user_id: str) -> bool:
        binding = await self._binding_manager.find_user_binding(user_id)
        if binding is None:
            return False
        await self._retire_binding(binding, DestroyReason.USER_DESTROYED)
        return True

    async def reclaim_idle_users(self) -> int:
        reclaimed = 0
        for binding in await self._binding_manager.expired_idle_user_bindings():
            await self._retire_binding(binding, DestroyReason.USER_IDLE_EXPIRED)
            reclaimed += 1
            self._metrics.increment("user_ttl_reclaims")
        return reclaimed

    async def reclaim_idle_sessions(self) -> int:
        return await self.reclaim_idle_users()

    async def _retire_binding(
        self, binding: UserSandboxBindingRecord, reason: DestroyReason
    ) -> None:
        async with self._destroy_lock:
            record = await self._repository.get(binding.sandbox_id)
            if record is None or record.state in (SandboxState.DESTROYED, SandboxState.LOST):
                return
            for lease in await self._lease_manager.active_turns_for_sandbox(binding.sandbox_id):
                try:
                    # _retire_binding already owns _destroy_lock. Do not let release recurse
                    # into retirement when user reuse is disabled.
                    await self.release(
                        lease.lease_id,
                        lease.fencing_token,
                        retire_when_reuse_disabled=False,
                    )
                except ServiceException:
                    self._metrics.increment("workspace_checkpoint_degraded")
            if record.state not in (SandboxState.RETIRING, SandboxState.DESTROYING):
                await self._repository.transition(
                    record.ref.sandbox_id, record.state, SandboxState.RETIRING
                )
            await self._destroy_record(record, reason)

    async def recover_expired(self) -> int:
        recovered = 0
        for lease in await self._lease_manager.expired_turn_leases():
            try:
                await self.release(lease.lease_id, lease.fencing_token)
            finally:
                recovered += 1
                self._metrics.increment("expired_lease_recoveries")
        return recovered

    async def shutdown(self) -> list[Exception]:
        errors: list[Exception] = []
        for binding in list(await self._binding_manager.user_bindings()):
            try:
                await self._retire_binding(binding, DestroyReason.PROVIDER_ERROR)
            except Exception as exc:
                errors.append(exc)
        records = await self._repository.records_in(
            [SandboxState.CREATING, SandboxState.WARMING, SandboxState.READY, SandboxState.DESTROYING]
        )
        for record in records:
            try:
                await self._destroy_record(record, DestroyReason.PROVIDER_ERROR)
            except Exception as exc:
                errors.append(exc)
        return errors

    async def _checkpoint_workspace(
        self,
        record: SandboxRecord,
        lease_id: str,
        fencing_token: int,
        user_id: str,
        session_id: str,
    ) -> None:
        started = monotonic()
        try:
            workspace_lock = self._workspace_locks.setdefault((user_id, session_id), asyncio.Lock())
            async with workspace_lock:
                snapshot = await self._provider.checkpoint_workspace(
                    record.ref, user_id, session_id, lease_id, fencing_token
                )
                await self._workspace_store.commit(snapshot, lease_id, fencing_token)
        except Exception as exc:
            self._metrics.increment("workspace_checkpoint_failures")
            error = ServiceException(SandboxErrorCode.WORKSPACE_SYNC_FAILED, "工作区 checkpoint 失败")
            error.__cause__ = exc
            raise error
        self._metrics.increment("workspace_checkpoint_successes")
        self._metrics.observe_ms("workspace_checkpoint", (monotonic() - started) * 1000)

    async def status(self, sandbox_id: str) -> SandboxRecord:
        record = await self._repository.get(sandbox_id)
        if record is None:
            raise ServiceException(SandboxErrorCode.LEASE_NOT_FOUND, f"沙箱 {sandbox_id} 不存在")
        return record

    async def _destroy_record(self, record: SandboxRecord, reason: DestroyReason) -> None:
        if record.state == SandboxState.DESTROYED:
            return
        if record.state != SandboxState.DESTROYING:
            await self._repository.transition(record.ref.sandbox_id, record.state, SandboxState.DESTROYING)
        last_error: Exception | None = None
        for attempt in range(self._destroy_max_retries):
            self._metrics.increment("destroy_attempts")
            started = monotonic()
            try:
                await asyncio.wait_for(
                    self._provider.destroy(record.ref, reason.value), timeout=self._destroy_timeout
                )
                self._metrics.observe_ms("destroy", (monotonic() - started) * 1000)
                self._metrics.increment("destroy_successes")
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self._destroy_max_retries:
                    await asyncio.sleep(self._destroy_backoff * (2**attempt))
        if last_error is not None:
            await self._repository.transition(
                record.ref.sandbox_id,
                SandboxState.DESTROYING,
                SandboxState.LOST,
                error=str(last_error)[:200],
            )
            self._metrics.increment("destroy_failures")
            raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE, "沙箱销毁失败") from last_error
        await self._repository.transition(
            record.ref.sandbox_id, SandboxState.DESTROYING, SandboxState.DESTROYED
        )
        await self._binding_manager.clear_binding(record)
