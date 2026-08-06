from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sandbox_v1.domain.entities import WorkspaceRecord


@dataclass
class _WorkspaceRepositoryState:
    """Shared in-memory Workspace state.

    This is the temporary implementation behind the repository port. The port
    shape matches the future Mongo authority so callers do not depend on
    process-local storage details.
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    records: dict[tuple[str, str], WorkspaceRecord] = field(default_factory=dict)
