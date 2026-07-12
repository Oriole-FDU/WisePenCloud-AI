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
    state_version: int = 0


@dataclass(frozen=True)
class SandboxLease:
    lease_id: str
    request_id: str
    sandbox_id: str
    tenant_id: str
    workspace_id: str
    expires_at: datetime
    endpoint: Endpoint | None = None


@dataclass(frozen=True)
class WorkspaceSnapshot:
    tenant_id: str
    workspace_id: str
    files: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionRequest:
    request_id: str
    tenant_id: str
    workspace_id: str
    operation: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionResult:
    request_id: str
    status: str
    data: dict[str, Any] = field(default_factory=dict)
