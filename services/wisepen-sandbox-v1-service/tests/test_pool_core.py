from __future__ import annotations

import pytest

from sandbox_v1.application.services.sandbox_pool import (
    PoolMaintenancePlan,
    SandboxPool,
)
from sandbox_v1.core.storage.memory import MemorySandboxRepository
from sandbox_v1.domain.entities import (
    Endpoint,
    PoolSnapshot,
    SandboxRecord,
    SandboxRef,
    SandboxState,
)


def test_maintenance_plan_counts_inflight_supply() -> None:
    snapshot = PoolSnapshot(
        generation=7,
        counts={
            SandboxState.READY: 1,
            SandboxState.WARMING: 1,
            SandboxState.CREATING: 1,
        },
        min_ready=1,
        target_ready=4,
    )

    plan = PoolMaintenancePlan.from_snapshot(
        snapshot,
        reserve=1,
        max_create_batch=3,
    )

    # In-flight containers already count toward the replenishment target.
    assert plan.deficit == 2
    assert plan.create_count == 2
    assert plan.should_replenish is True


@pytest.mark.asyncio
async def test_consume_ready_assigns_and_reuses_user_binding() -> None:
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository, min_ready=1, target_ready=1)
    record = SandboxRecord(
        ref=SandboxRef(
            sandbox_id="sandbox-a",
            provider_id="container-a",
            endpoint=Endpoint(base_url="http://127.0.0.1:8080"),
        ),
        state=SandboxState.READY,
    )
    await repository.save(record)

    consumed = await pool.consume("user-a")
    reused = await pool.consume("user-a")

    assert consumed.ref.sandbox_id == "sandbox-a"
    assert consumed.state == SandboxState.USER_ACTIVE
    assert reused.ref.sandbox_id == consumed.ref.sandbox_id
    assert reused.state == SandboxState.USER_ACTIVE

    assert reused.owner_user_id == "user-a"
    assert reused.reuse_count == 1
