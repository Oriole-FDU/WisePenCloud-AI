from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sandbox_v1.domain.entities import (
    SandboxRecord,
    UserSandboxBindingRecord,
)
from sandbox_v1.domain.interfaces.metrics import MetricsPort


@dataclass
class _RepositoryState:
    """Shared in-memory state for pool records and user bindings."""

    metrics: MetricsPort
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    records: dict[str, SandboxRecord] = field(default_factory=dict)
    user_bindings: dict[str, UserSandboxBindingRecord] = field(default_factory=dict)
    sandbox_bindings: dict[str, str] = field(default_factory=dict)

    generation: int = 0
    empty_checkouts: int = 0
