from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SandboxState(StrEnum):
    CREATING = "creating"
    WARMING = "warming"
    READY = "ready"
    ALLOCATED = "allocated"
    RUNNING = "running"
    SYNCING = "syncing"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    LOST = "lost"


class DestroyReason(StrEnum):
    ALLOCATION_FAILED = "allocation_failed"
    LEASE_RELEASED = "lease_released"
    WARMUP_FAILED = "warmup_failed"
    WARMUP_TIMEOUT = "warmup_timeout"
    LEASE_EXPIRED = "lease_expired"
    DESTROY_TIMEOUT = "destroy_timeout"
    PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True)
class Health:
    healthy: bool
    status: str = "unknown"
    version: str | None = None


@dataclass(frozen=True)
class SandboxSpec:
    image: str
    cpu_cores: float | None = None
    memory_mb: int | None = None
    timeout_ms: int | None = None
    network_enabled: bool = False
    environment: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Endpoint:
    base_url: str
    token: str | None = None


@dataclass(frozen=True)
class SandboxRef:
    sandbox_id: str
    provider_id: str
    endpoint: Endpoint | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SandboxRecord:
    ref: SandboxRef
    state: SandboxState
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    lease_id: str | None = None
    request_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    lease_expires_at: datetime | None = None
    fencing_token: int = 0
    state_version: int = 0
    last_error: str | None = None


@dataclass(frozen=True)
class LeaseRecord:
    lease_id: str
    request_id: str
    sandbox_id: str
    tenant_id: str
    workspace_id: str
    expires_at: datetime
    fencing_token: int
    endpoint: Endpoint | None = None

    def as_lease(self) -> "SandboxLease":
        return SandboxLease(
            lease_id=self.lease_id,
            request_id=self.request_id,
            sandbox_id=self.sandbox_id,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            expires_at=self.expires_at,
            fencing_token=self.fencing_token,
            endpoint=self.endpoint,
        )


@dataclass(frozen=True)
class SandboxLease:
    lease_id: str
    request_id: str
    sandbox_id: str
    tenant_id: str
    workspace_id: str
    expires_at: datetime
    fencing_token: int
    endpoint: Endpoint | None = None


@dataclass(frozen=True)
class WorkspaceSnapshot:
    tenant_id: str
    workspace_id: str
    files: dict[str, str] = field(default_factory=dict)
    deleted_files: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ExecutionRequest:
    request_id: str
    tenant_id: str
    workspace_id: str
    operation: str
    payload: dict[str, Any] = field(default_factory=dict)
    fencing_token: int = 0
    operation_id: str | None = None


@dataclass(frozen=True)
class ExecutionResult:
    request_id: str
    status: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PoolSnapshot:
    generation: int
    counts: dict[SandboxState, int]
    empty_checkouts: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "empty_checkouts": self.empty_checkouts,
            **{state.value: self.counts.get(state, 0) for state in SandboxState},
        }
