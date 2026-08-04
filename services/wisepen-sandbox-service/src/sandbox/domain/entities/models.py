from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SandboxState(StrEnum):
    # 预热链路：Watcher 创建容器后等待 AIO 健康，再放回 READY 池。
    CREATING = "creating"
    WARMING = "warming"
    READY = "ready"
    # 用户链路：READY 被用户绑定后可同时承接该用户的多个 session。
    ALLOCATED = "allocated"
    USER_ACTIVE = "user_active"
    USER_IDLE = "user_idle"
    RETIRING = "retiring"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    # 丢失状态表示销毁或预热补偿失败，后续只能由运维/恢复流程处理。
    LOST = "lost"


class DestroyReason(StrEnum):
    ALLOCATION_FAILED = "allocation_failed"
    LEASE_RELEASED = "lease_released"
    WARMUP_FAILED = "warmup_failed"
    WARMUP_TIMEOUT = "warmup_timeout"
    LEASE_EXPIRED = "lease_expired"
    DESTROY_TIMEOUT = "destroy_timeout"
    PROVIDER_ERROR = "provider_error"
    USER_DESTROYED = "user_destroyed"
    USER_IDLE_EXPIRED = "user_idle_expired"
    USER_LRU_EVICTED = "user_lru_evicted"


@dataclass(frozen=True)
class Health:
    healthy: bool
    status: str = "unknown"
    version: str | None = None
    attempts: int = 0


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
    public_vnc_url: str | None = None
    public_websocket_url: str | None = None


@dataclass(frozen=True)
class SandboxRef:
    sandbox_id: str
    provider_id: str
    endpoint: Endpoint | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveredSandbox:
    """Runtime-discovered container used only for startup reconciliation.

    Labels are discovery hints because Docker labels are immutable after create.
    Repository state remains authoritative for ownership, binding and generation.
    """

    ref: SandboxRef
    labels: dict[str, str] = field(default_factory=dict)
    running: bool = True


@dataclass
class SandboxRecord:
    ref: SandboxRef
    state: SandboxState
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    owner_user_id: str | None = None
    user_binding_id: str | None = None
    active_turn_count: int = 0
    vnc_ref_count: int = 0
    # 状态版本参与 readiness_token，防止旧健康检查把新状态误放回 READY。
    state_version: int = 0
    last_error: str | None = None
    readiness_token: str | None = None
    reuse_count: int = 0


@dataclass
class UserSandboxBindingRecord:
    """Stable ownership of one container by one user."""

    user_binding_id: str
    sandbox_id: str
    user_id: str
    container_generation: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_active_at: datetime = field(default_factory=utc_now)
    idle_expires_at: datetime | None = None
    reuse_count: int = 0


@dataclass
class SessionWorkspaceRecord:
    """A session directory resident in one generation of a user's container."""

    user_id: str
    session_id: str
    sandbox_id: str
    container_generation: int
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_checkpoint_at: datetime | None = None
    dirty: bool = False
    last_error: str | None = None


@dataclass
class TurnLeaseRecord:
    """Short-lived, fenced lease for exactly one Chat/VNC turn."""

    lease_id: str
    request_id: str
    sandbox_id: str
    tenant_id: str
    workspace_id: str
    expires_at: datetime
    fencing_token: int
    user_binding_id: str
    container_reused: bool = False
    workspace_reused: bool = False
    created_at: datetime = field(default_factory=utc_now)
    closing_at: datetime | None = None
    released_at: datetime | None = None


@dataclass(frozen=True)
class LeaseRecord:
    lease_id: str
    request_id: str
    sandbox_id: str
    tenant_id: str
    workspace_id: str
    expires_at: datetime
    fencing_token: int
    user_binding_id: str = ""
    user_idle_expires_at: datetime | None = None
    container_reused: bool = False
    workspace_reused: bool = False
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
            user_binding_id=self.user_binding_id,
            user_idle_expires_at=self.user_idle_expires_at,
            container_reused=self.container_reused,
            workspace_reused=self.workspace_reused,
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
    user_binding_id: str = ""
    user_idle_expires_at: datetime | None = None
    container_reused: bool = False
    workspace_reused: bool = False
    endpoint: Endpoint | None = None


@dataclass(frozen=True)
class WorkspaceSnapshot:
    # 工作区快照表示完整缓存快照；提交时会整体替换旧目录。
    tenant_id: str
    workspace_id: str
    files: dict[str, str | bytes] = field(default_factory=dict)


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
    metrics: dict[str, Any] = field(default_factory=dict)
    min_ready: int = 0
    target_ready: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "empty_checkouts": self.empty_checkouts,
            "min_ready": self.min_ready,
            "target_ready": self.target_ready,
            **self.metrics,
            **{state.value: self.counts.get(state, 0) for state in SandboxState},
        }
