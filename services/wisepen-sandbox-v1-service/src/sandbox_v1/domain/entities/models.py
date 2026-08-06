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
    USER_ACTIVE = "user_active"
    RETIRING = "retiring"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    LOST = "lost"


@dataclass(frozen=True)
class Health:
    healthy: bool
    status: str = "unknown"
    version: str | None = None
    attempts: int = 0


@dataclass(frozen=True)
class SandboxSpec:
    """Provider-neutral container request used by the pool watcher."""

    image: str
    cpu_cores: float | None = None
    memory_mb: int | None = None
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


@dataclass(frozen=True)
class DiscoveredSandbox:
    """Runtime-discovered container used for startup reconciliation."""

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
    state_version: int = 0
    last_error: str | None = None
    reuse_count: int = 0


@dataclass
class UserSandboxBindingRecord:
    """Stable ownership of one retained container by one user."""

    user_binding_id: str
    sandbox_id: str
    user_id: str
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    last_active_at: datetime = field(default_factory=utc_now)
    reuse_count: int = 0


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
