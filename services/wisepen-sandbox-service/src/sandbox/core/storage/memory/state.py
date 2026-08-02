from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sandbox.domain.entities import (
    SandboxRecord,
    SessionWorkspaceRecord,
    TurnLeaseRecord,
    UserSandboxBindingRecord,
)
from sandbox.domain.interfaces.metrics import MetricsPort


@dataclass
class _RepositoryState:
    """Shared in-memory state for all repository sub-managers.

    A single lock serialises all mutations so that composite operations
    (e.g. *checkout_ready*) remain atomic across binding, workspace and
    lease indices.
    """

    metrics: MetricsPort
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    records: dict[str, SandboxRecord] = field(default_factory=dict)
    user_bindings: dict[str, UserSandboxBindingRecord] = field(default_factory=dict)
    sandbox_bindings: dict[str, str] = field(default_factory=dict)
    workspaces: dict[tuple[str, str], SessionWorkspaceRecord] = field(default_factory=dict)
    turn_leases: dict[str, TurnLeaseRecord] = field(default_factory=dict)
    requests: dict[str, str] = field(default_factory=dict)
    active_sessions: dict[tuple[str, str], str] = field(default_factory=dict)

    generation: int = 0
    empty_checkouts: int = 0
    next_fencing_token: int = 0
