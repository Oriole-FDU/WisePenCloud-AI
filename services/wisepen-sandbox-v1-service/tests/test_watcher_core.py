from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from sandbox_v1.application.services.sandbox_pool import SandboxPool
from sandbox_v1.application.services.sandbox_watcher import Watcher
from sandbox_v1.core.storage.memory import MemorySandboxRepository
from sandbox_v1.domain.entities import (
    DiscoveredSandbox,
    Endpoint,
    Health,
    SandboxRef,
    SandboxSpec,
    SandboxState,
)


@dataclass
class FakeProvider:
    """Provider fake used to test pool behavior without a runtime adapter."""

    created: int = 0
    destroyed: list[tuple[str, str]] = field(default_factory=list)

    async def validate_deployment(self) -> None:
        return None

    async def create(self, spec: SandboxSpec) -> SandboxRef:
        self.created += 1
        return SandboxRef(
            sandbox_id=f"sandbox-{self.created}",
            provider_id=f"provider-{self.created}",
            endpoint=Endpoint(base_url=f"http://sandbox-{self.created}"),
        )

    async def wait_ready(self, sandbox: SandboxRef, timeout_seconds: float) -> Health:
        return Health(True, "ok", attempts=1)

    async def health(self, sandbox: SandboxRef) -> Health:
        return Health(True, "ok", attempts=1)

    async def list_managed(self) -> list[DiscoveredSandbox]:
        return []

    async def cleanup_owned(self) -> int:
        return 0

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        self.destroyed.append((sandbox.sandbox_id, reason))


@pytest.mark.asyncio
async def test_watcher_replenishes_only_the_pool_deficit() -> None:
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository, min_ready=1, target_ready=2)
    provider = FakeProvider()
    watcher = Watcher(
        pool,
        repository,
        provider,
        SandboxSpec(image="test-image"),
        min_ready=1,
        max_create_batch=2,
    )

    assert await watcher.reconcile() == 2
    snapshot = await pool.snapshot()
    assert snapshot.counts[SandboxState.READY] == 2
    assert provider.created == 2

    assert await watcher.reconcile() == 0
    assert provider.created == 2
