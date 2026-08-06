from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    """返回带 UTC timezone 的当前时间，供记录时间戳使用。"""

    return datetime.now(timezone.utc)


class SandboxState(StrEnum):
    """沙箱生命周期状态。

    CREATING/WARMING/READY 属于池供给路径，USER_ACTIVE/RETIRING 属于用户绑定
    路径，DESTROYING/DESTROYED/LOST 属于清理和失败落态路径。
    """

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
    """provider 返回的容器健康状态。"""

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
    """沙箱实例对服务内部暴露的访问入口。"""

    base_url: str
    token: str | None = None


@dataclass(frozen=True)
class SandboxRef:
    """跨 Repository 和 Provider 传递的沙箱引用。"""

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
    """Repository 中的沙箱权威记录。"""

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
    """某一时刻的池状态和指标快照。"""

    generation: int
    counts: dict[SandboxState, int]
    empty_checkouts: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    min_ready: int = 0
    target_ready: int = 0

    def as_dict(self) -> dict[str, Any]:
        """展开为 API/日志友好的 dict，包括固定字段、指标和各状态计数。"""

        return {
            # 固定快照字段。
            "generation": self.generation,
            "empty_checkouts": self.empty_checkouts,
            "min_ready": self.min_ready,
            "target_ready": self.target_ready,
            # 指标收集器输出。
            **self.metrics,
            # 每个状态按 state.value 展开，缺失状态按 0 输出。
            **{state.value: self.counts.get(state, 0) for state in SandboxState},
        }
