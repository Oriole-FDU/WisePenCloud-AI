from __future__ import annotations

from datetime import datetime
from typing import Iterable

from common.core.exceptions import ServiceException
from sandbox.core.observability.metrics import MetricsCollector
from sandbox.core.storage.memory.binding_manager import MemoryBindingManager
from sandbox.core.storage.memory.lease_manager import MemoryLeaseManager
from sandbox.core.storage.memory.state import _RepositoryState
from sandbox.core.storage.memory.workspace_manager import MemoryWorkspaceManager
from sandbox.domain.entities import (
    LeaseRecord,
    PoolSnapshot,
    SandboxRecord,
    SandboxState,
    TurnLeaseRecord,
    UserSandboxBindingRecord,
    utc_now,
)
from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.domain.interfaces.metrics import MetricsPort


_ALLOWED_TRANSITIONS: dict[SandboxState, frozenset[SandboxState]] = {
    SandboxState.CREATING: frozenset({SandboxState.WARMING, SandboxState.DESTROYING}),
    SandboxState.WARMING: frozenset({SandboxState.READY, SandboxState.DESTROYING}),
    SandboxState.READY: frozenset({SandboxState.ALLOCATED, SandboxState.DESTROYING}),
    SandboxState.ALLOCATED: frozenset(
        {SandboxState.USER_ACTIVE, SandboxState.RETIRING, SandboxState.DESTROYING}
    ),
    SandboxState.USER_ACTIVE: frozenset(
        {SandboxState.USER_IDLE, SandboxState.RETIRING, SandboxState.DESTROYING}
    ),
    SandboxState.USER_IDLE: frozenset(
        {SandboxState.USER_ACTIVE, SandboxState.RETIRING, SandboxState.DESTROYING}
    ),
    SandboxState.RETIRING: frozenset({SandboxState.DESTROYING}),
    SandboxState.DESTROYING: frozenset({SandboxState.DESTROYED, SandboxState.LOST}),
    SandboxState.DESTROYED: frozenset(),
    SandboxState.LOST: frozenset(),
}


class MemorySandboxRepository:
    """Single-process authority for user containers, session folders and turn leases.

    Internally delegates to three focused sub-managers that share a single
    ``_RepositoryState`` (one lock, one set of indices), keeping composite
    operations such as *checkout_ready* atomic.
    """

    def __init__(self, metrics: MetricsPort | None = None) -> None:
        state = _RepositoryState(metrics=metrics or MetricsCollector())
        self._state = state
        self._bindings = MemoryBindingManager(state)
        self._leases = MemoryLeaseManager(state)
        self._workspaces = MemoryWorkspaceManager(state)

    # -- public sub-manager access (for DI) ----------------------------------

    @property
    def lease_manager(self) -> MemoryLeaseManager:
        return self._leases

    @property
    def binding_manager(self) -> MemoryBindingManager:
        return self._bindings

    @property
    def workspace_manager(self) -> MemoryWorkspaceManager:
        return self._workspaces

    @property
    def metrics(self) -> MetricsPort:
        return self._state.metrics

    # -- SandboxRepository: record CRUD -------------------------------------

    async def save(self, record: SandboxRecord) -> None:
        async with self._state.lock:
            self._state.records[record.ref.sandbox_id] = record
            self._state.generation += 1

    async def get(self, sandbox_id: str) -> SandboxRecord | None:
        async with self._state.lock:
            return self._state.records.get(sandbox_id)

    async def records_in(
        self, states: Iterable[SandboxState]
    ) -> list[SandboxRecord]:
        wanted = set(states)
        async with self._state.lock:
            return [
                record
                for record in self._state.records.values()
                if record.state in wanted
            ]

    async def snapshot(
        self, *, min_ready: int = 0, target_ready: int = 0
    ) -> PoolSnapshot:
        async with self._state.lock:
            counts = {s: 0 for s in SandboxState}
            for record in self._state.records.values():
                counts[record.state] += 1
            ready = counts[SandboxState.READY]
            active = [
                lease
                for lease in self._state.turn_leases.values()
                if lease.released_at is None
            ]
            self._state.metrics.set_value(
                "active_user_bindings", counts[SandboxState.USER_ACTIVE]
            )
            self._state.metrics.set_value(
                "idle_user_bindings", counts[SandboxState.USER_IDLE]
            )
            self._state.metrics.set_value("active_turn_leases", len(active))
            self._state.metrics.set_value(
                "zombie_leases",
                sum(lease.expires_at <= utc_now() for lease in active),
            )
            return PoolSnapshot(
                self._state.generation,
                counts,
                self._state.empty_checkouts,
                self._state.metrics.snapshot(ready, min_ready, target_ready),
                min_ready,
                target_ready,
            )

    async def transition(
        self,
        sandbox_id: str,
        expected: SandboxState,
        state: SandboxState,
        *,
        error: str | None = None,
    ) -> SandboxRecord:
        async with self._state.lock:
            record = self._state.records.get(sandbox_id)
            if record is None:
                raise ServiceException(
                    SandboxErrorCode.LEASE_NOT_FOUND, f"沙箱 {sandbox_id} 不存在"
                )
            if (
                record.state != expected
                or state not in _ALLOWED_TRANSITIONS[expected]
            ):
                raise ServiceException(
                    SandboxErrorCode.INVALID_STATE_TRANSITION,
                    f"不能从 {record.state.value} 转换到 {state.value}",
                )
            record.state = state
            record.state_version += 1
            record.updated_at = utc_now()
            record.last_error = error
            self._state.generation += 1
            return record

    # -- SandboxRepository: checkout -----------------------------------------

    async def checkout_ready(
        self,
        request_id: str,
        user_id: str,
        session_id: str,
        lease_ttl_seconds: int,
        user_idle_ttl_seconds: int = 600,
        max_user_bindings: int = 20,
    ) -> tuple[SandboxRecord, LeaseRecord]:
        async with self._state.lock:
            # -- idempotency ---------------------------------------------------
            existing_id = self._state.requests.get(request_id)
            if existing_id:
                existing = self._state.turn_leases.get(existing_id)
                if existing is None:
                    raise ServiceException(
                        SandboxErrorCode.LEASE_EXPIRED,
                        "request_id 对应的租约已不存在",
                    )
                if (
                    existing.tenant_id != user_id
                    or existing.workspace_id != session_id
                ):
                    raise ServiceException(
                        SandboxErrorCode.REQUEST_CONFLICT,
                        "request_id 上下文与已有租约不一致",
                    )
                if (
                    existing.released_at is not None
                    or existing.closing_at is not None
                ):
                    raise ServiceException(
                        SandboxErrorCode.LEASE_EXPIRED,
                        "request_id 对应的租约已关闭",
                    )
                record = self._state.records.get(existing.sandbox_id)
                binding = self._state.user_bindings.get(user_id)
                if record is None or binding is None:
                    raise ServiceException(
                        SandboxErrorCode.LEASE_EXPIRED,
                        "沙箱或用户绑定已被清理",
                    )
                return record, MemoryLeaseManager._lease_record(
                    record, existing, binding
                )

            # -- session-busy guard -------------------------------------------
            session_key = (user_id, session_id)
            active_id = self._state.active_sessions.get(session_key)
            if (
                active_id
                and self._state.turn_leases[active_id].released_at is None
            ):
                self._state.metrics.increment("session_busy_rejections")
                raise ServiceException(
                    SandboxErrorCode.SESSION_BUSY, "同一 session 已有活动 turn"
                )

            # -- resolve or create binding ------------------------------------
            binding = self._state.user_bindings.get(user_id)
            container_reused = binding is not None
            if binding is None:
                if len(self._state.user_bindings) >= max_user_bindings:
                    raise ServiceException(
                        SandboxErrorCode.USER_SANDBOX_CAPACITY,
                        "用户沙箱容器容量已满",
                    )
                record = next(
                    (
                        item
                        for item in self._state.records.values()
                        if item.state == SandboxState.READY
                    ),
                    None,
                )
                if record is None:
                    self._state.empty_checkouts += 1
                    self._state.metrics.increment("pool_empty_checkouts")
                    raise ServiceException(
                        SandboxErrorCode.POOL_EMPTY, "沙箱池暂无可用实例"
                    )
                record.state = SandboxState.ALLOCATED
                record.state_version += 1
                binding = self._bindings._new_binding(
                    record.ref.sandbox_id, user_id
                )
                record.owner_user_id = user_id
                record.user_binding_id = binding.user_binding_id
            else:
                record = self._state.records[binding.sandbox_id]
                if record.state not in (
                    SandboxState.USER_ACTIVE,
                    SandboxState.USER_IDLE,
                ):
                    raise ServiceException(
                        SandboxErrorCode.SANDBOX_UNAVAILABLE,
                        "用户沙箱当前不可用",
                    )
                if record.state == SandboxState.USER_IDLE:
                    record.state = SandboxState.USER_ACTIVE
                    record.state_version += 1
                binding.reuse_count += 1
                record.reuse_count = binding.reuse_count
                self._state.metrics.increment("user_container_reuse_hits")

            # -- resolve or create workspace ----------------------------------
            workspace = self._state.workspaces.get(session_key)
            workspace_reused = bool(
                workspace
                and workspace.sandbox_id == record.ref.sandbox_id
                and workspace.container_generation == binding.container_generation
            )
            if workspace is None or not workspace_reused:
                workspace = self._workspaces._upsert_workspace(
                    user_id,
                    session_id,
                    record.ref.sandbox_id,
                    binding.container_generation,
                    reused=False,
                )

            # -- create lease -------------------------------------------------
            lease = self._leases._new_lease(
                request_id,
                user_id,
                session_id,
                record.ref.sandbox_id,
                binding,
                lease_ttl_seconds,
                container_reused,
                workspace_reused,
            )
            record.active_turn_count += 1
            record.updated_at = lease.created_at
            binding.updated_at = lease.created_at
            binding.last_active_at = lease.created_at
            binding.idle_expires_at = None
            self._state.generation += 1
            return record, MemoryLeaseManager._lease_record(record, lease, binding)

    # -- SandboxRepository: pool readiness ------------------------------------

    async def prepare_ready(
        self, record: SandboxRecord, readiness_token: str
    ) -> int:
        async with self._state.lock:
            current = self._state.records.get(record.ref.sandbox_id)
            if current is None or current.state != SandboxState.WARMING:
                raise ServiceException(
                    SandboxErrorCode.INVALID_STATE_TRANSITION,
                    "只有 warming 状态沙箱可以准备 readiness",
                )
            record.readiness_token = readiness_token
            self._state.records[record.ref.sandbox_id] = record
            self._state.generation += 1
            return self._state.generation

    async def return_ready(
        self, sandbox_id: str, health_token: str, expected_generation: int
    ) -> SandboxRecord:
        async with self._state.lock:
            record = self._state.records.get(sandbox_id)
            if record is None:
                raise ServiceException(
                    SandboxErrorCode.LEASE_NOT_FOUND,
                    f"沙箱 {sandbox_id} 不存在",
                )
            if self._state.generation != expected_generation:
                raise ServiceException(
                    SandboxErrorCode.FENCING_REJECTED,
                    "沙箱池 generation 已过期",
                )
            if record.state != SandboxState.WARMING:
                raise ServiceException(
                    SandboxErrorCode.INVALID_STATE_TRANSITION,
                    "只有 warming 状态沙箱可以回到 ready",
                )
            if (
                sandbox_id in self._state.sandbox_bindings
                or record.owner_user_id
                or record.active_turn_count
            ):
                raise ServiceException(
                    SandboxErrorCode.FENCING_REJECTED,
                    "沙箱仍有用户绑定或活动租约",
                )
            if record.readiness_token != health_token:
                raise ServiceException(
                    SandboxErrorCode.FENCING_REJECTED,
                    "沙箱健康 token 非法",
                )
            record.state = SandboxState.READY
            record.readiness_token = None
            record.state_version += 1
            record.updated_at = utc_now()
            self._state.generation += 1
            self._state.metrics.increment("ready_returns")
            return record

    async def records_older_than(
        self, state: SandboxState, cutoff: datetime
    ) -> list[SandboxRecord]:
        async with self._state.lock:
            return [
                record
                for record in self._state.records.values()
                if record.state == state and record.updated_at <= cutoff
            ]
