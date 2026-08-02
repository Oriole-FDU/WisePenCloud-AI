from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Iterable

from common.core.exceptions import ServiceException
from sandbox.core.observability.metrics import MetricsCollector
from sandbox.domain.entities import (
    LeaseRecord,
    PoolSnapshot,
    SandboxRecord,
    SandboxState,
    SessionWorkspaceRecord,
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
    SandboxState.ALLOCATED: frozenset({SandboxState.USER_ACTIVE, SandboxState.RETIRING, SandboxState.DESTROYING}),
    SandboxState.USER_ACTIVE: frozenset({SandboxState.USER_IDLE, SandboxState.RETIRING, SandboxState.DESTROYING}),
    SandboxState.USER_IDLE: frozenset({SandboxState.USER_ACTIVE, SandboxState.RETIRING, SandboxState.DESTROYING}),
    SandboxState.RUNNING: frozenset({SandboxState.RETIRING, SandboxState.DESTROYING}),
    SandboxState.CHECKPOINTING: frozenset({SandboxState.RETIRING, SandboxState.DESTROYING}),
    SandboxState.SESSION_IDLE: frozenset({SandboxState.RETIRING, SandboxState.DESTROYING}),
    SandboxState.SYNCING: frozenset({SandboxState.RETIRING, SandboxState.DESTROYING}),
    SandboxState.RETIRING: frozenset({SandboxState.DESTROYING}),
    SandboxState.DESTROYING: frozenset({SandboxState.DESTROYED, SandboxState.LOST}),
    SandboxState.DESTROYED: frozenset(),
    SandboxState.LOST: frozenset(),
}


class MemorySandboxRepository:
    """Single-process authority for user containers, session folders and turn leases."""

    def __init__(self, metrics: MetricsPort | None = None) -> None:
        self._records: dict[str, SandboxRecord] = {}
        self._user_bindings: dict[str, UserSandboxBindingRecord] = {}
        self._sandbox_bindings: dict[str, str] = {}
        self._workspaces: dict[tuple[str, str], SessionWorkspaceRecord] = {}
        self._turn_leases: dict[str, TurnLeaseRecord] = {}
        self._requests: dict[str, str] = {}
        self._active_sessions: dict[tuple[str, str], str] = {}
        self._generation = 0
        self._empty_checkouts = 0
        self._next_fencing_token = 0
        self._lock = asyncio.Lock()
        self.metrics = metrics or MetricsCollector()

    async def save(self, record: SandboxRecord) -> None:
        async with self._lock:
            self._records[record.ref.sandbox_id] = record
            self._generation += 1

    async def get(self, sandbox_id: str) -> SandboxRecord | None:
        async with self._lock:
            return self._records.get(sandbox_id)

    async def records_in(self, states: Iterable[SandboxState]) -> list[SandboxRecord]:
        wanted = set(states)
        async with self._lock:
            return [record for record in self._records.values() if record.state in wanted]

    async def snapshot(self, *, min_ready: int = 0, target_ready: int = 0) -> PoolSnapshot:
        async with self._lock:
            counts = {state: 0 for state in SandboxState}
            for record in self._records.values():
                counts[record.state] += 1
            ready = counts[SandboxState.READY]
            active = [lease for lease in self._turn_leases.values() if lease.released_at is None]
            self.metrics.set_value("active_user_bindings", counts[SandboxState.USER_ACTIVE])
            self.metrics.set_value("idle_user_bindings", counts[SandboxState.USER_IDLE])
            self.metrics.set_value("active_turn_leases", len(active))
            self.metrics.set_value("zombie_leases", sum(lease.expires_at <= utc_now() for lease in active))
            return PoolSnapshot(
                self._generation,
                counts,
                self._empty_checkouts,
                self.metrics.snapshot(ready, min_ready, target_ready),
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
        async with self._lock:
            record = self._records.get(sandbox_id)
            if record is None:
                raise ServiceException(SandboxErrorCode.LEASE_NOT_FOUND, f"沙箱 {sandbox_id} 不存在")
            if record.state != expected or state not in _ALLOWED_TRANSITIONS[expected]:
                raise ServiceException(
                    SandboxErrorCode.INVALID_STATE_TRANSITION,
                    f"不能从 {record.state.value} 转换到 {state.value}",
                )
            record.state = state
            record.state_version += 1
            record.updated_at = utc_now()
            record.last_error = error
            self._generation += 1
            return record

    async def checkout_ready(
        self,
        request_id: str,
        user_id: str,
        session_id: str,
        lease_ttl_seconds: int,
        user_idle_ttl_seconds: int = 600,
        max_user_bindings: int = 20,
    ) -> tuple[SandboxRecord, LeaseRecord]:
        async with self._lock:
            existing_id = self._requests.get(request_id)
            if existing_id:
                existing = self._turn_leases[existing_id]
                if existing.tenant_id != user_id or existing.workspace_id != session_id:
                    raise ServiceException(SandboxErrorCode.REQUEST_CONFLICT, "request_id 上下文与已有租约不一致")
                if existing.released_at is not None or existing.closing_at is not None:
                    raise ServiceException(SandboxErrorCode.LEASE_EXPIRED, "request_id 对应的租约已关闭")
                record = self._records[existing.sandbox_id]
                binding = self._user_bindings[user_id]
                return record, self._lease_for(record, existing, binding)

            session_key = (user_id, session_id)
            active_id = self._active_sessions.get(session_key)
            if active_id and self._turn_leases[active_id].released_at is None:
                self.metrics.increment("session_busy_rejections")
                raise ServiceException(SandboxErrorCode.SESSION_BUSY, "同一 session 已有活动 turn")

            binding = self._user_bindings.get(user_id)
            container_reused = binding is not None
            if binding is None:
                if len(self._user_bindings) >= max_user_bindings:
                    raise ServiceException(SandboxErrorCode.USER_SANDBOX_CAPACITY, "用户沙箱容器容量已满")
                record = next((item for item in self._records.values() if item.state == SandboxState.READY), None)
                if record is None:
                    self._empty_checkouts += 1
                    self.metrics.increment("pool_empty_checkouts")
                    raise ServiceException(SandboxErrorCode.POOL_EMPTY, "沙箱池暂无可用实例")
                record.state = SandboxState.ALLOCATED
                record.state_version += 1
                binding = UserSandboxBindingRecord(
                    user_binding_id=f"user_{uuid.uuid4().hex}",
                    sandbox_id=record.ref.sandbox_id,
                    user_id=user_id,
                )
                self._user_bindings[user_id] = binding
                self._sandbox_bindings[record.ref.sandbox_id] = user_id
                record.owner_user_id = user_id
                record.user_binding_id = binding.user_binding_id
                self.metrics.increment("user_bindings_created")
            else:
                record = self._records[binding.sandbox_id]
                if record.state not in (SandboxState.USER_ACTIVE, SandboxState.USER_IDLE):
                    raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE, "用户沙箱当前不可用")
                if record.state == SandboxState.USER_IDLE:
                    record.state = SandboxState.USER_ACTIVE
                    record.state_version += 1
                binding.reuse_count += 1
                record.reuse_count = binding.reuse_count
                self.metrics.increment("user_container_reuse_hits")

            workspace = self._workspaces.get(session_key)
            workspace_reused = bool(
                workspace
                and workspace.sandbox_id == record.ref.sandbox_id
                and workspace.container_generation == binding.container_generation
            )
            if workspace is None or not workspace_reused:
                workspace = SessionWorkspaceRecord(
                    user_id=user_id,
                    session_id=session_id,
                    sandbox_id=record.ref.sandbox_id,
                    container_generation=binding.container_generation,
                )
                self._workspaces[session_key] = workspace

            self._next_fencing_token += 1
            now = utc_now()
            lease = TurnLeaseRecord(
                lease_id=f"lease_{uuid.uuid4().hex}",
                request_id=request_id,
                sandbox_id=record.ref.sandbox_id,
                tenant_id=user_id,
                workspace_id=session_id,
                expires_at=now + timedelta(seconds=lease_ttl_seconds),
                fencing_token=self._next_fencing_token,
                user_binding_id=binding.user_binding_id,
                container_reused=container_reused,
                workspace_reused=workspace_reused,
            )
            self._turn_leases[lease.lease_id] = lease
            self._requests[request_id] = lease.lease_id
            self._active_sessions[session_key] = lease.lease_id
            record.active_turn_count += 1
            record.updated_at = now
            binding.updated_at = now
            binding.last_active_at = now
            binding.idle_expires_at = None
            self.metrics.lease_started(user_id)
            self.metrics.increment("allocate_successes")
            self._generation += 1
            return record, self._lease_for(record, lease, binding)

    @staticmethod
    def _lease_for(
        record: SandboxRecord, lease: TurnLeaseRecord, binding: UserSandboxBindingRecord
    ) -> LeaseRecord:
        return LeaseRecord(
            lease_id=lease.lease_id,
            request_id=lease.request_id,
            sandbox_id=lease.sandbox_id,
            tenant_id=lease.tenant_id,
            workspace_id=lease.workspace_id,
            expires_at=lease.expires_at,
            fencing_token=lease.fencing_token,
            user_binding_id=binding.user_binding_id,
            user_idle_expires_at=binding.idle_expires_at,
            container_reused=lease.container_reused,
            workspace_reused=lease.workspace_reused,
            endpoint=record.ref.endpoint,
        )

    async def activate_user_binding(self, sandbox_id: str) -> SandboxRecord:
        async with self._lock:
            record = self._records.get(sandbox_id)
            if record is None:
                raise ServiceException(SandboxErrorCode.LEASE_NOT_FOUND, "用户沙箱不存在")
            if record.state == SandboxState.ALLOCATED:
                record.state = SandboxState.USER_ACTIVE
                record.state_version += 1
                record.updated_at = utc_now()
                self._generation += 1
            return record

    async def find_lease(self, lease_id: str) -> SandboxRecord:
        async with self._lock:
            lease = self._turn_leases.get(lease_id)
            record = self._records.get(lease.sandbox_id) if lease else None
            if record is None:
                raise ServiceException(SandboxErrorCode.LEASE_NOT_FOUND, f"租约 {lease_id} 不存在")
            return record

    async def get_turn_lease(self, lease_id: str) -> TurnLeaseRecord:
        async with self._lock:
            lease = self._turn_leases.get(lease_id)
            if lease is None:
                raise ServiceException(SandboxErrorCode.LEASE_NOT_FOUND, f"租约 {lease_id} 不存在")
            return lease

    async def find_turn_request(self, request_id: str) -> TurnLeaseRecord | None:
        async with self._lock:
            lease_id = self._requests.get(request_id)
            return self._turn_leases.get(lease_id) if lease_id else None

    async def active_turn_for_session(self, user_id: str, session_id: str) -> TurnLeaseRecord | None:
        async with self._lock:
            lease_id = self._active_sessions.get((user_id, session_id))
            lease = self._turn_leases.get(lease_id) if lease_id else None
            return lease if lease and lease.released_at is None else None

    async def active_turns_for_sandbox(self, sandbox_id: str) -> list[TurnLeaseRecord]:
        async with self._lock:
            return [
                lease for lease in self._turn_leases.values()
                if lease.sandbox_id == sandbox_id and lease.released_at is None
            ]

    async def find_user_binding(self, user_id: str) -> UserSandboxBindingRecord | None:
        async with self._lock:
            return self._user_bindings.get(user_id)

    async def binding_for_sandbox(self, sandbox_id: str) -> UserSandboxBindingRecord | None:
        async with self._lock:
            user_id = self._sandbox_bindings.get(sandbox_id)
            return self._user_bindings.get(user_id) if user_id else None

    async def user_bindings(self) -> list[UserSandboxBindingRecord]:
        async with self._lock:
            return list(self._user_bindings.values())

    async def idle_user_bindings(self, now: datetime | None = None) -> list[UserSandboxBindingRecord]:
        async with self._lock:
            values = [
                binding for binding in self._user_bindings.values()
                if (record := self._records.get(binding.sandbox_id)) is not None
                and record.state == SandboxState.USER_IDLE
            ]
            return sorted(values, key=lambda item: item.last_active_at)

    async def expired_idle_user_bindings(self, now: datetime | None = None) -> list[UserSandboxBindingRecord]:
        current = now or utc_now()
        return [
            binding for binding in await self.idle_user_bindings(current)
            if binding.idle_expires_at is not None and binding.idle_expires_at <= current
        ]

    async def find_workspace(self, user_id: str, session_id: str) -> SessionWorkspaceRecord | None:
        async with self._lock:
            return self._workspaces.get((user_id, session_id))

    async def workspaces_for_user(self, user_id: str) -> list[SessionWorkspaceRecord]:
        async with self._lock:
            return [workspace for key, workspace in self._workspaces.items() if key[0] == user_id]

    async def mark_workspace_prepared(self, user_id: str, session_id: str) -> SessionWorkspaceRecord:
        async with self._lock:
            workspace = self._workspaces[(user_id, session_id)]
            workspace.updated_at = utc_now()
            workspace.last_error = None
            self._generation += 1
            return workspace

    async def mark_workspace_dirty(self, user_id: str, session_id: str) -> None:
        async with self._lock:
            workspace = self._workspaces.get((user_id, session_id))
            if workspace:
                workspace.dirty = True
                workspace.updated_at = utc_now()
                self._generation += 1

    async def remove_workspace(self, user_id: str, session_id: str) -> bool:
        async with self._lock:
            removed = self._workspaces.pop((user_id, session_id), None) is not None
            self._generation += int(removed)
            return removed

    async def close_lease(self, lease_id: str, fencing_token: int) -> SandboxRecord:
        async with self._lock:
            lease = self._turn_leases.get(lease_id)
            record = self._records.get(lease.sandbox_id) if lease else None
            if lease is None or record is None:
                raise ServiceException(SandboxErrorCode.LEASE_NOT_FOUND, f"租约 {lease_id} 不存在")
            if lease.fencing_token != fencing_token:
                raise ServiceException(SandboxErrorCode.FENCING_REJECTED, "租约 fencing token 已过期")
            if lease.released_at is None and lease.closing_at is None:
                lease.closing_at = utc_now()
                self._generation += 1
            return record

    async def validate_lease(
        self,
        lease_id: str,
        user_id: str,
        session_id: str,
        fencing_token: int,
        *,
        now: datetime | None = None,
    ) -> SandboxRecord:
        async with self._lock:
            lease = self._turn_leases.get(lease_id)
            record = self._records.get(lease.sandbox_id) if lease else None
            if lease is None or record is None:
                raise ServiceException(SandboxErrorCode.LEASE_NOT_FOUND, f"租约 {lease_id} 不存在")
            if lease.released_at is not None or lease.closing_at is not None:
                raise ServiceException(SandboxErrorCode.LEASE_EXPIRED, "沙箱租约已关闭")
            if (
                lease.tenant_id != user_id
                or lease.workspace_id != session_id
                or lease.fencing_token != fencing_token
            ):
                raise ServiceException(SandboxErrorCode.FENCING_REJECTED, "租约上下文或 fencing token 不匹配")
            if lease.expires_at <= (now or utc_now()):
                raise ServiceException(SandboxErrorCode.LEASE_EXPIRED, "沙箱租约已过期")
            if record.state != SandboxState.USER_ACTIVE:
                raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE, "用户沙箱未运行")
            return record

    async def finish_release(
        self, lease_id: str, idle_ttl_seconds: int, *, error: str | None = None
    ) -> SandboxRecord:
        async with self._lock:
            lease = self._turn_leases.get(lease_id)
            record = self._records.get(lease.sandbox_id) if lease else None
            if lease is None or record is None:
                raise ServiceException(SandboxErrorCode.LEASE_NOT_FOUND, f"租约 {lease_id} 不存在")
            if lease.released_at is not None:
                return record
            now = utc_now()
            lease.released_at = now
            self._active_sessions.pop((lease.tenant_id, lease.workspace_id), None)
            record.active_turn_count = max(0, record.active_turn_count - 1)
            record.updated_at = now
            record.last_error = error
            workspace = self._workspaces.get((lease.tenant_id, lease.workspace_id))
            if workspace:
                workspace.updated_at = now
                workspace.last_error = error
                if error is None:
                    workspace.dirty = False
                    workspace.last_checkpoint_at = now
            binding = self._user_bindings.get(lease.tenant_id)
            if binding:
                binding.updated_at = now
                binding.last_active_at = now
                if record.active_turn_count == 0:
                    record.state = SandboxState.USER_IDLE
                    record.state_version += 1
                    binding.idle_expires_at = now + timedelta(seconds=idle_ttl_seconds)
                else:
                    record.state = SandboxState.USER_ACTIVE
                    binding.idle_expires_at = None
            self.metrics.lease_finished(lease.tenant_id)
            self._generation += 1
            return record

    async def clear_binding(self, record: SandboxRecord) -> None:
        async with self._lock:
            user_id = self._sandbox_bindings.pop(record.ref.sandbox_id, None)
            if user_id:
                self._user_bindings.pop(user_id, None)
                for key in [key for key in self._workspaces if key[0] == user_id]:
                    self._workspaces.pop(key, None)
            for lease_id, lease in list(self._turn_leases.items()):
                if lease.sandbox_id != record.ref.sandbox_id:
                    continue
                if lease.released_at is None:
                    self.metrics.lease_finished(lease.tenant_id)
                self._active_sessions.pop((lease.tenant_id, lease.workspace_id), None)
                self._requests.pop(lease.request_id, None)
                self._turn_leases.pop(lease_id, None)
            record.owner_user_id = None
            record.user_binding_id = None
            record.active_turn_count = 0
            record.vnc_ref_count = 0
            self._generation += 1

    async def prepare_ready(self, record: SandboxRecord, readiness_token: str) -> int:
        async with self._lock:
            current = self._records.get(record.ref.sandbox_id)
            if current is None or current.state != SandboxState.WARMING:
                raise ServiceException(SandboxErrorCode.INVALID_STATE_TRANSITION, "只有 warming 状态沙箱可以准备 readiness")
            record.readiness_token = readiness_token
            self._records[record.ref.sandbox_id] = record
            self._generation += 1
            return self._generation

    async def return_ready(
        self, sandbox_id: str, health_token: str, expected_generation: int
    ) -> SandboxRecord:
        async with self._lock:
            record = self._records.get(sandbox_id)
            if record is None:
                raise ServiceException(SandboxErrorCode.LEASE_NOT_FOUND, f"沙箱 {sandbox_id} 不存在")
            if self._generation != expected_generation:
                raise ServiceException(SandboxErrorCode.FENCING_REJECTED, "沙箱池 generation 已过期")
            if record.state != SandboxState.WARMING:
                raise ServiceException(SandboxErrorCode.INVALID_STATE_TRANSITION, "只有 warming 状态沙箱可以回到 ready")
            if sandbox_id in self._sandbox_bindings or record.owner_user_id or record.active_turn_count:
                raise ServiceException(SandboxErrorCode.FENCING_REJECTED, "沙箱仍有用户绑定或活动租约")
            if record.readiness_token != health_token:
                raise ServiceException(SandboxErrorCode.FENCING_REJECTED, "沙箱健康 token 非法")
            record.state = SandboxState.READY
            record.readiness_token = None
            record.state_version += 1
            record.updated_at = utc_now()
            self._generation += 1
            self.metrics.increment("ready_returns")
            return record

    async def expired_turn_leases(self, now: datetime | None = None) -> list[TurnLeaseRecord]:
        current = now or utc_now()
        async with self._lock:
            return [
                lease for lease in self._turn_leases.values()
                if lease.released_at is None and lease.expires_at <= current
            ]

    async def records_older_than(self, state: SandboxState, cutoff: datetime) -> list[SandboxRecord]:
        async with self._lock:
            return [
                record for record in self._records.values()
                if record.state == state and record.updated_at <= cutoff
            ]
