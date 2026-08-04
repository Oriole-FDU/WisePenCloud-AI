from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from sandbox_v1.application.services.sandbox_startup_reconciler import (
    SandboxStartupReconciler,
)
from sandbox_v1.core.storage.memory import MemorySandboxRepository
from sandbox_v1.domain.entities import (
    DiscoveredSandbox,
    Endpoint,
    Health,
    SandboxRecord,
    SandboxRef,
    SandboxState,
)


@dataclass
class FakeProvider:
    discovered: list[DiscoveredSandbox]
    destroyed: list[tuple[str, str]] = field(default_factory=list)

    async def list_managed(self) -> list[DiscoveredSandbox]:
        return self.discovered

    async def health(self, sandbox: SandboxRef) -> Health:
        return Health(healthy=True, status="ok")

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        self.destroyed.append((sandbox.sandbox_id, reason))


def _ref(sandbox_id: str) -> SandboxRef:
    return SandboxRef(
        sandbox_id=sandbox_id,
        provider_id=f"container-{sandbox_id}",
        endpoint=Endpoint(base_url="http://127.0.0.1:8080"),
    )


@pytest.mark.asyncio
async def test_startup_reconciler_keeps_healthy_authoritative_ready() -> None:
    repository = MemorySandboxRepository()
    await repository.save(SandboxRecord(ref=_ref("ready-a"), state=SandboxState.READY))
    provider = FakeProvider([DiscoveredSandbox(ref=_ref("ready-a"), running=True)])

    result = await SandboxStartupReconciler(repository, provider).reconcile()

    assert result.matched_ready == 1
    assert provider.destroyed == []


@pytest.mark.asyncio
async def test_startup_reconciler_destroys_orphan_container() -> None:
    repository = MemorySandboxRepository()
    provider = FakeProvider([DiscoveredSandbox(ref=_ref("orphan-a"), running=True)])

    result = await SandboxStartupReconciler(repository, provider).reconcile()

    assert result.orphan_destroyed == 1
    assert provider.destroyed == [("orphan-a", "startup_orphan")]
