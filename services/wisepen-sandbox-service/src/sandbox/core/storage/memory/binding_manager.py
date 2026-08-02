from __future__ import annotations

import uuid
from datetime import datetime

from common.core.exceptions import ServiceException
from sandbox.core.storage.memory.state import _RepositoryState
from sandbox.domain.entities import (
    SandboxRecord,
    SandboxState,
    UserSandboxBindingRecord,
    utc_now,
)
from sandbox.domain.error_codes import SandboxErrorCode


class MemoryBindingManager:
    """In-memory user-container binding operations."""

    def __init__(self, state: _RepositoryState) -> None:
        self._state = state

    # -- factories called by the composite checkout -----------------------

    def _new_binding(self, sandbox_id: str, user_id: str) -> UserSandboxBindingRecord:
        binding = UserSandboxBindingRecord(
            user_binding_id=f"user_{uuid.uuid4().hex}",
            sandbox_id=sandbox_id,
            user_id=user_id,
        )
        self._state.user_bindings[user_id] = binding
        self._state.sandbox_bindings[sandbox_id] = user_id
        self._state.metrics.increment("user_bindings_created")
        return binding

    # -- public read / query -------------------------------------------------

    async def find_user_binding(self, user_id: str) -> UserSandboxBindingRecord | None:
        async with self._state.lock:
            return self._state.user_bindings.get(user_id)

    async def binding_for_sandbox(self, sandbox_id: str) -> UserSandboxBindingRecord | None:
        async with self._state.lock:
            user_id = self._state.sandbox_bindings.get(sandbox_id)
            return self._state.user_bindings.get(user_id) if user_id else None

    async def user_bindings(self) -> list[UserSandboxBindingRecord]:
        async with self._state.lock:
            return list(self._state.user_bindings.values())

    async def idle_user_bindings(
        self, now: datetime | None = None
    ) -> list[UserSandboxBindingRecord]:
        async with self._state.lock:
            values = [
                binding
                for binding in self._state.user_bindings.values()
                if (record := self._state.records.get(binding.sandbox_id)) is not None
                and record.state == SandboxState.USER_IDLE
            ]
            return sorted(values, key=lambda item: item.last_active_at)

    async def expired_idle_user_bindings(
        self, now: datetime | None = None
    ) -> list[UserSandboxBindingRecord]:
        current = now or utc_now()
        return [
            binding
            for binding in await self.idle_user_bindings(current)
            if binding.idle_expires_at is not None and binding.idle_expires_at <= current
        ]

    # -- lifecycle -----------------------------------------------------------

    async def activate_user_binding(self, sandbox_id: str) -> SandboxRecord:
        async with self._state.lock:
            record = self._state.records.get(sandbox_id)
            if record is None:
                raise ServiceException(
                    SandboxErrorCode.LEASE_NOT_FOUND, "用户沙箱不存在"
                )
            if record.state == SandboxState.ALLOCATED:
                record.state = SandboxState.USER_ACTIVE
                record.state_version += 1
                record.updated_at = utc_now()
                self._state.generation += 1
            return record

    async def clear_binding(self, record: SandboxRecord) -> None:
        async with self._state.lock:
            user_id = self._state.sandbox_bindings.pop(record.ref.sandbox_id, None)
            if user_id:
                self._state.user_bindings.pop(user_id, None)
                for key in [key for key in self._state.workspaces if key[0] == user_id]:
                    self._state.workspaces.pop(key, None)
            for lease_id, lease in list(self._state.turn_leases.items()):
                if lease.sandbox_id != record.ref.sandbox_id:
                    continue
                if lease.released_at is None:
                    self._state.metrics.lease_finished(lease.tenant_id)
                self._state.active_sessions.pop(
                    (lease.tenant_id, lease.workspace_id), None
                )
                self._state.requests.pop(lease.request_id, None)
                self._state.turn_leases.pop(lease_id, None)
            record.owner_user_id = None
            record.user_binding_id = None
            record.active_turn_count = 0
            record.vnc_ref_count = 0
            self._state.generation += 1
