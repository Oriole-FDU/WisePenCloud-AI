from __future__ import annotations

from dataclasses import dataclass

from common.core.exceptions import ServiceException

from sandbox_v1.domain.entities import (
    PoolSnapshot,
    SandboxRecord,
    SandboxState,
)
from sandbox_v1.domain.error_codes import SandboxErrorCode
from sandbox_v1.domain.repositories import SandboxRepository


@dataclass(frozen=True)
class PoolMaintenancePlan:
    """Result of one pool-capacity evaluation."""

    ready: int
    warming: int
    creating: int
    target_ready: int
    reserve: int
    max_create_batch: int
    deficit: int
    create_count: int

    @classmethod
    def from_snapshot(
        cls,
        snapshot: PoolSnapshot,
        *,
        reserve: int,
        max_create_batch: int,
    ) -> "PoolMaintenancePlan":
        ready = snapshot.counts.get(SandboxState.READY, 0)
        warming = snapshot.counts.get(SandboxState.WARMING, 0)
        creating = snapshot.counts.get(SandboxState.CREATING, 0)
        reserve = max(0, reserve)
        max_create_batch = max(1, max_create_batch)
        deficit = max(
            0,
            snapshot.target_ready + reserve - ready - warming - creating,
        )
        return cls(
            ready=ready,
            warming=warming,
            creating=creating,
            target_ready=snapshot.target_ready,
            reserve=reserve,
            max_create_batch=max_create_batch,
            deficit=deficit,
            create_count=min(deficit, max_create_batch),
        )

    @property
    def should_replenish(self) -> bool:
        return self.create_count > 0


class SandboxPool:
    """Pool facade for capacity planning and user consumption."""

    def __init__(
        self,
        repository: SandboxRepository,
        min_ready: int = 1,
        target_ready: int = 2,
        max_user_bindings: int = 20,
    ) -> None:
        self._repository = repository
        self._min_ready = min_ready
        self._target_ready = target_ready
        self._max_user_bindings = max_user_bindings

    async def consume(self, user_id: str) -> SandboxRecord:
        """Assign one pool container to the user without operation ownership state."""
        if not user_id or not user_id.strip():
            raise ServiceException(
                SandboxErrorCode.INVALID_CONSUME_REQUEST,
                "user identifier is required",
            )
        return await self._repository.checkout_ready(
            user_id,
            self._max_user_bindings,
        )

    async def snapshot(self) -> PoolSnapshot:
        return await self._repository.snapshot(
            min_ready=self._min_ready,
            target_ready=self._target_ready,
        )

    async def maintenance_plan(
        self, *, reserve: int = 0, max_create_batch: int = 1
    ) -> PoolMaintenancePlan:
        snapshot = await self.snapshot()
        return PoolMaintenancePlan.from_snapshot(
            snapshot,
            reserve=reserve,
            max_create_batch=max_create_batch,
        )
