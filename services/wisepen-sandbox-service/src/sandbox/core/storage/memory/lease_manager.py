from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from common.core.exceptions import ServiceException
from sandbox.core.storage.memory.state import _RepositoryState
from sandbox.domain.entities import (
    LeaseRecord,
    SandboxRecord,
    SandboxState,
    TurnLeaseRecord,
    UserSandboxBindingRecord,
    utc_now,
)
from sandbox.domain.error_codes import SandboxErrorCode


class MemoryLeaseManager:
    """In-memory turn-lease operations."""

    def __init__(self, state: _RepositoryState) -> None:
        self._state = state

    # -- factories called by the composite checkout -----------------------

    def _new_lease(
        self,
        request_id: str,
        user_id: str,
        session_id: str,
        sandbox_id: str,
        binding: UserSandboxBindingRecord,
        lease_ttl_seconds: int,
        container_reused: bool,
        workspace_reused: bool,
    ) -> TurnLeaseRecord:
        self._state.next_fencing_token += 1
        now = utc_now()
        lease = TurnLeaseRecord(
            lease_id=f"lease_{uuid.uuid4().hex}",
            request_id=request_id,
            sandbox_id=sandbox_id,
            tenant_id=user_id,
            workspace_id=session_id,
            expires_at=now + timedelta(seconds=lease_ttl_seconds),
            fencing_token=self._state.next_fencing_token,
            user_binding_id=binding.user_binding_id,
            container_reused=container_reused,
            workspace_reused=workspace_reused,
        )
        self._state.turn_leases[lease.lease_id] = lease
        self._state.requests[request_id] = lease.lease_id
        self._state.active_sessions[(user_id, session_id)] = lease.lease_id
        self._state.metrics.lease_started(user_id)
        self._state.metrics.increment("allocate_successes")
        return lease

    @staticmethod
    def _lease_record(
        record: SandboxRecord,
        lease: TurnLeaseRecord,
        binding: UserSandboxBindingRecord,
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

    # -- public read / query -------------------------------------------------

    async def find_lease(self, lease_id: str) -> SandboxRecord:
        async with self._state.lock:
            lease = self._state.turn_leases.get(lease_id)
            record = self._state.records.get(lease.sandbox_id) if lease else None
            if record is None:
                raise ServiceException(
                    SandboxErrorCode.LEASE_NOT_FOUND, f"租约 {lease_id} 不存在"
                )
            return record

    async def get_turn_lease(self, lease_id: str) -> TurnLeaseRecord:
        async with self._state.lock:
            lease = self._state.turn_leases.get(lease_id)
            if lease is None:
                raise ServiceException(
                    SandboxErrorCode.LEASE_NOT_FOUND, f"租约 {lease_id} 不存在"
                )
            return lease

    async def find_turn_request(self, request_id: str) -> TurnLeaseRecord | None:
        async with self._state.lock:
            lease_id = self._state.requests.get(request_id)
            return self._state.turn_leases.get(lease_id) if lease_id else None

    async def active_turn_for_session(
        self, user_id: str, session_id: str
    ) -> TurnLeaseRecord | None:
        async with self._state.lock:
            lease_id = self._state.active_sessions.get((user_id, session_id))
            lease = self._state.turn_leases.get(lease_id) if lease_id else None
            return lease if lease and lease.released_at is None else None

    async def active_turns_for_sandbox(self, sandbox_id: str) -> list[TurnLeaseRecord]:
        async with self._state.lock:
            return [
                lease
                for lease in self._state.turn_leases.values()
                if lease.sandbox_id == sandbox_id and lease.released_at is None
            ]

    async def expired_turn_leases(
        self, now: datetime | None = None
    ) -> list[TurnLeaseRecord]:
        current = now or utc_now()
        async with self._state.lock:
            return [
                lease
                for lease in self._state.turn_leases.values()
                if lease.released_at is None and lease.expires_at <= current
            ]

    # -- lifecycle -----------------------------------------------------------

    async def close_lease(self, lease_id: str, fencing_token: int) -> SandboxRecord:
        async with self._state.lock:
            lease = self._state.turn_leases.get(lease_id)
            record = self._state.records.get(lease.sandbox_id) if lease else None
            if lease is None or record is None:
                raise ServiceException(
                    SandboxErrorCode.LEASE_NOT_FOUND, f"租约 {lease_id} 不存在"
                )
            if lease.fencing_token != fencing_token:
                raise ServiceException(
                    SandboxErrorCode.FENCING_REJECTED, "租约 fencing token 已过期"
                )
            if lease.released_at is None and lease.closing_at is None:
                lease.closing_at = utc_now()
                self._state.generation += 1
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
        async with self._state.lock:
            lease = self._state.turn_leases.get(lease_id)
            record = self._state.records.get(lease.sandbox_id) if lease else None
            if lease is None or record is None:
                raise ServiceException(
                    SandboxErrorCode.LEASE_NOT_FOUND, f"租约 {lease_id} 不存在"
                )
            if lease.released_at is not None or lease.closing_at is not None:
                raise ServiceException(
                    SandboxErrorCode.LEASE_EXPIRED, "沙箱租约已关闭"
                )
            if (
                lease.tenant_id != user_id
                or lease.workspace_id != session_id
                or lease.fencing_token != fencing_token
            ):
                raise ServiceException(
                    SandboxErrorCode.FENCING_REJECTED,
                    "租约上下文或 fencing token 不匹配",
                )
            if lease.expires_at <= (now or utc_now()):
                raise ServiceException(
                    SandboxErrorCode.LEASE_EXPIRED, "沙箱租约已过期"
                )
            if record.state != SandboxState.USER_ACTIVE:
                raise ServiceException(
                    SandboxErrorCode.SANDBOX_UNAVAILABLE, "用户沙箱未运行"
                )
            return record

    async def finish_release(
        self, lease_id: str, idle_ttl_seconds: int, *, error: str | None = None
    ) -> SandboxRecord:
        async with self._state.lock:
            lease = self._state.turn_leases.get(lease_id)
            record = self._state.records.get(lease.sandbox_id) if lease else None
            if lease is None or record is None:
                raise ServiceException(
                    SandboxErrorCode.LEASE_NOT_FOUND, f"租约 {lease_id} 不存在"
                )
            if lease.released_at is not None:
                return record
            now = utc_now()
            lease.released_at = now
            self._state.active_sessions.pop(
                (lease.tenant_id, lease.workspace_id), None
            )
            record.active_turn_count = max(0, record.active_turn_count - 1)
            record.updated_at = now
            record.last_error = error
            workspace = self._state.workspaces.get(
                (lease.tenant_id, lease.workspace_id)
            )
            if workspace:
                workspace.updated_at = now
                workspace.last_error = error
                if error is None:
                    workspace.dirty = False
                    workspace.last_checkpoint_at = now
            binding = self._state.user_bindings.get(lease.tenant_id)
            if binding:
                binding.updated_at = now
                binding.last_active_at = now
                if record.active_turn_count == 0:
                    record.state = SandboxState.USER_IDLE
                    record.state_version += 1
                    binding.idle_expires_at = now + timedelta(
                        seconds=idle_ttl_seconds
                    )
                else:
                    record.state = SandboxState.USER_ACTIVE
                    binding.idle_expires_at = None
            self._state.metrics.lease_finished(lease.tenant_id)
            self._state.generation += 1
            return record
