from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from common.core.exceptions import ServiceException

from sandbox.application.services import (
    SandboxPool,
    SandboxScheduler,
    SandboxStartupReconciler,
    Watcher,
)
from sandbox.core.storage.memory import MemorySandboxRepository
from sandbox.domain.entities import (
    DiscoveredSandbox,
    Endpoint,
    ExecutionRequest,
    ExecutionResult,
    Health,
    SandboxRecord,
    SandboxRef,
    SandboxSpec,
    SandboxState,
    WorkspaceSnapshot,
    utc_now,
)
from sandbox.domain.error_codes import SandboxErrorCode


class FakeWorkspace:
    def __init__(self) -> None:
        self.commits: list[tuple[WorkspaceSnapshot, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self.snapshots: dict[tuple[str, str], WorkspaceSnapshot] = {}

    async def snapshot(self, tenant_id: str, workspace_id: str) -> WorkspaceSnapshot:
        return self.snapshots.get(
            (tenant_id, workspace_id), WorkspaceSnapshot(tenant_id, workspace_id)
        )

    async def commit(
        self, snapshot: WorkspaceSnapshot, lease_id: str, fencing_token: int = 0
    ) -> None:
        self.commits.append((snapshot, lease_id))
        self.snapshots[(snapshot.tenant_id, snapshot.workspace_id)] = snapshot

    async def delete(self, tenant_id: str, workspace_id: str) -> None:
        self.deleted.append((tenant_id, workspace_id))
        self.snapshots.pop((tenant_id, workspace_id), None)


class FakeProvider:
    def __init__(self) -> None:
        self.created = 0
        self.destroyed: list[str] = []
        self.prepared_snapshots: list[WorkspaceSnapshot] = []
        self.deleted_workspaces: list[tuple[str, str]] = []
        self.exported_files: dict[str, str] = {"result.txt": "done"}
        self.fail_prepare = False
        self.checkpoints: list[tuple[str, int]] = []
        self.forward_started: list[str] = []

    async def validate_deployment(self) -> None: ...

    async def create(self, spec: SandboxSpec) -> SandboxRef:
        self.created += 1
        return SandboxRef(
            sandbox_id=f"sb-{self.created}",
            provider_id=f"provider-{self.created}",
            endpoint=Endpoint(f"http://127.0.0.1:{8000 + self.created}"),
        )

    async def wait_ready(self, sandbox: SandboxRef, timeout_seconds: float) -> Health:
        return Health(True, "ready")

    async def health(self, sandbox: SandboxRef) -> Health:
        return Health(True, "ready")

    async def prepare_workspace(self, sandbox: SandboxRef, workspace: WorkspaceSnapshot) -> None:
        self.prepared_snapshots.append(workspace)
        if self.fail_prepare:
            raise RuntimeError("prepare failed")

    async def activate(self, sandbox: SandboxRef, lease) -> Endpoint:
        assert sandbox.endpoint is not None
        return sandbox.endpoint

    async def forward(self, sandbox: SandboxRef, request: ExecutionRequest) -> ExecutionResult:
        self.forward_started.append(request.workspace_id)
        delay = float(request.payload.get("delay", 0))
        if delay:
            await asyncio.sleep(delay)
        return ExecutionResult(request.request_id, "succeeded", {"ok": True})

    async def export_workspace(
        self, sandbox: SandboxRef, tenant_id: str, workspace_id: str
    ) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(tenant_id, workspace_id, dict(self.exported_files))

    async def checkpoint_workspace(
        self,
        sandbox: SandboxRef,
        tenant_id: str,
        workspace_id: str,
        lease_id: str,
        fencing_token: int,
    ) -> WorkspaceSnapshot:
        self.checkpoints.append((lease_id, fencing_token))
        return WorkspaceSnapshot(tenant_id, workspace_id, dict(self.exported_files))

    async def delete_workspace(
        self, sandbox: SandboxRef, tenant_id: str, workspace_id: str
    ) -> None:
        self.deleted_workspaces.append((tenant_id, workspace_id))

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        self.destroyed.append(sandbox.sandbox_id)


class ReconcileProvider:
    def __init__(
        self,
        discovered: list[DiscoveredSandbox],
        *,
        healthy: bool = True,
    ) -> None:
        self.discovered = discovered
        self.healthy = healthy
        self.health_checks: list[str] = []
        self.destroyed: list[tuple[str, str]] = []

    async def list_managed(self) -> list[DiscoveredSandbox]:
        return list(self.discovered)

    async def health(self, sandbox: SandboxRef) -> Health:
        self.health_checks.append(sandbox.sandbox_id)
        return Health(self.healthy, "ready" if self.healthy else "unhealthy")

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        self.destroyed.append((sandbox.sandbox_id, reason))


def discovered_sandbox(
    sandbox_id: str,
    *,
    provider_id: str | None = None,
    running: bool = True,
) -> DiscoveredSandbox:
    return DiscoveredSandbox(
        SandboxRef(
            sandbox_id,
            provider_id or f"provider-{sandbox_id}",
            Endpoint(f"http://{sandbox_id}:8080"),
        ),
        labels={"wisepen.binding": "unbound"},
        running=running,
    )


async def add_ready(provider: FakeProvider, repository: MemorySandboxRepository) -> None:
    pool = SandboxPool(repository)
    record = SandboxRecord(
        ref=await provider.create(SandboxSpec("test")), state=SandboxState.WARMING
    )
    await pool.add_ready(record)


async def ready_pool(provider: FakeProvider):
    repository = MemorySandboxRepository()
    await add_ready(provider, repository)
    return repository, SandboxPool(repository)


@pytest.mark.asyncio
async def test_same_user_sessions_share_container_but_use_distinct_leases() -> None:
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())

    first = await scheduler.allocate("req-a", "user-1", "session-a")
    second = await scheduler.allocate("req-b", "user-1", "session-b")

    assert first.sandbox_id == second.sandbox_id
    assert first.lease_id != second.lease_id
    assert first.fencing_token != second.fencing_token
    assert first.container_reused is False
    assert second.container_reused is True
    assert (await scheduler.status(first.sandbox_id)).active_turn_count == 2


@pytest.mark.asyncio
async def test_different_users_never_share_container() -> None:
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    await add_ready(provider, repository)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())

    first = await scheduler.allocate("req-a", "user-1", "session")
    second = await scheduler.allocate("req-b", "user-2", "session")
    assert first.sandbox_id != second.sandbox_id


@pytest.mark.asyncio
async def test_same_session_second_request_is_rejected_immediately() -> None:
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())
    first = await scheduler.allocate("req-a", "user-1", "session")

    with pytest.raises(ServiceException) as exc_info:
        await scheduler.allocate("req-b", "user-1", "session")
    assert exc_info.value.code == SandboxErrorCode.SESSION_BUSY.code
    assert await scheduler.allocate("req-a", "user-1", "session") == first


@pytest.mark.asyncio
async def test_different_sessions_execute_concurrently_without_global_lock() -> None:
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())
    long_lease = await scheduler.allocate("req-a", "user-1", "session-a")
    short_lease = await scheduler.allocate("req-b", "user-1", "session-b")

    long_task = asyncio.create_task(scheduler.execute(
        long_lease.lease_id,
        ExecutionRequest("long", "user-1", "session-a", "shell_exec", {"delay": 0.15}, long_lease.fencing_token),
    ))
    await asyncio.sleep(0.01)
    await scheduler.execute(
        short_lease.lease_id,
        ExecutionRequest("short", "user-1", "session-b", "shell_exec", {}, short_lease.fencing_token),
    )
    assert not long_task.done()
    await long_task


@pytest.mark.asyncio
async def test_release_only_idles_container_after_last_turn() -> None:
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())
    first = await scheduler.allocate("req-a", "user-1", "session-a")
    second = await scheduler.allocate("req-b", "user-1", "session-b")

    await scheduler.release(first.lease_id, first.fencing_token)
    assert (await scheduler.status(first.sandbox_id)).state == SandboxState.USER_ACTIVE
    await scheduler.release(second.lease_id, second.fencing_token)
    record = await scheduler.status(first.sandbox_id)
    assert record.state == SandboxState.USER_IDLE
    assert record.active_turn_count == 0
    assert provider.destroyed == []


@pytest.mark.asyncio
async def test_reuse_disabled_release_destroys_without_recursive_locking() -> None:
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(
        pool, repository, provider, FakeWorkspace(),
        user_reuse_enabled=False,
    )
    lease = await scheduler.allocate("req", "user-1", "session")

    await asyncio.wait_for(
        scheduler.release(lease.lease_id, lease.fencing_token), timeout=1
    )
    assert provider.destroyed == [lease.sandbox_id]


@pytest.mark.asyncio
async def test_release_reuses_resident_workspace_without_restore() -> None:
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())
    first = await scheduler.allocate("req-a", "user-1", "session")
    await scheduler.release(first.lease_id, first.fencing_token)
    second = await scheduler.allocate("req-b", "user-1", "session")

    assert second.sandbox_id == first.sandbox_id
    assert second.workspace_reused is True
    assert len(provider.prepared_snapshots) == 1


@pytest.mark.asyncio
async def test_checkpoint_failure_keeps_healthy_user_container() -> None:
    class FailingWorkspace(FakeWorkspace):
        async def commit(self, snapshot, lease_id, fencing_token=0):
            raise RuntimeError("store unavailable")

    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FailingWorkspace())
    lease = await scheduler.allocate("req", "user-1", "session")
    await scheduler.release(lease.lease_id, lease.fencing_token)

    record = await scheduler.status(lease.sandbox_id)
    workspace = await repository.workspace_manager.find_workspace("user-1", "session")
    assert record.state == SandboxState.USER_IDLE
    assert record.last_error
    assert workspace is not None and workspace.last_error
    assert provider.destroyed == []


@pytest.mark.asyncio
async def test_delete_workspace_does_not_destroy_user_container() -> None:
    provider = FakeProvider()
    store = FakeWorkspace()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, store)
    lease = await scheduler.allocate("req", "user-1", "session")

    assert await scheduler.delete_workspace("user-1", "session") is True
    assert await scheduler.delete_workspace("user-1", "session") is True
    assert provider.deleted_workspaces[-1] == ("user-1", "session")
    assert store.deleted[-1] == ("user-1", "session")
    assert provider.destroyed == []
    assert (await scheduler.status(lease.sandbox_id)).state == SandboxState.USER_IDLE


@pytest.mark.asyncio
async def test_idle_ttl_and_lru_destroy_only_idle_user_containers() -> None:
    provider = FakeProvider()
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(
        pool, repository, provider, FakeWorkspace(),
        max_user_bindings=1,
    )
    first = await scheduler.allocate("req-a", "user-1", "session")
    await scheduler.release(first.lease_id, first.fencing_token)
    binding = await repository.binding_manager.find_user_binding("user-1")
    assert binding is not None
    binding.idle_expires_at = utc_now() - timedelta(seconds=1)
    assert await scheduler.reclaim_idle_users() == 1
    assert provider.destroyed == [first.sandbox_id]

    await add_ready(provider, repository)
    second = await scheduler.allocate("req-b", "user-2", "session")
    assert second.sandbox_id != first.sandbox_id


@pytest.mark.asyncio
async def test_allocation_failure_destroys_new_user_container() -> None:
    provider = FakeProvider()
    provider.fail_prepare = True
    repository, pool = await ready_pool(provider)
    scheduler = SandboxScheduler(pool, repository, provider, FakeWorkspace())

    with pytest.raises(ServiceException):
        await scheduler.allocate("req", "user-1", "session")
    assert provider.destroyed == ["sb-1"]


@pytest.mark.asyncio
async def test_pool_maintenance_plan_counts_ready_and_inflight_warmups() -> None:
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository, target_ready=3)
    await repository.save(
        SandboxRecord(SandboxRef("ready", "provider"), SandboxState.READY)
    )
    await repository.save(
        SandboxRecord(SandboxRef("warming", "provider"), SandboxState.WARMING)
    )
    await repository.save(
        SandboxRecord(SandboxRef("creating", "provider"), SandboxState.CREATING)
    )

    plan = await pool.maintenance_plan(reserve=1, max_create_batch=5)

    assert plan.ready == 1
    assert plan.warming == 1
    assert plan.creating == 1
    assert plan.target_ready == 3
    assert plan.reserve == 1
    assert plan.deficit == 1
    assert plan.create_count == 1
    assert plan.should_replenish is True


@pytest.mark.asyncio
async def test_pool_maintenance_plan_caps_replenish_batch() -> None:
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository, target_ready=4)

    plan = await pool.maintenance_plan(max_create_batch=2)

    assert plan.deficit == 4
    assert plan.create_count == 2


@pytest.mark.asyncio
async def test_pool_consume_creates_replenish_demand_for_watcher() -> None:
    provider = FakeProvider()
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository, min_ready=1, target_ready=2)
    watcher = Watcher(
        pool, repository, provider, SandboxSpec("test"), target_ready=2, min_ready=1
    )
    assert await watcher.reconcile() == 2

    record, lease = await pool.consume("req", "user-1", "session")
    plan = await pool.maintenance_plan(max_create_batch=2)

    assert record.state == SandboxState.ALLOCATED
    assert lease.sandbox_id == record.ref.sandbox_id
    assert plan.ready == 1
    assert plan.deficit == 1
    assert plan.create_count == 1
    assert await watcher.reconcile() == 1
    assert (await pool.snapshot()).counts[SandboxState.READY] == 2


@pytest.mark.asyncio
async def test_startup_reconciler_destroys_orphan_discovered_container() -> None:
    repository = MemorySandboxRepository()
    provider = ReconcileProvider([discovered_sandbox("sb-orphan")])
    reconciler = SandboxStartupReconciler(repository, provider)

    result = await reconciler.reconcile()

    assert result.orphan_destroyed == 1
    assert provider.destroyed == [("sb-orphan", "startup_orphan")]


@pytest.mark.asyncio
async def test_startup_reconciler_keeps_healthy_ready_authoritative_record() -> None:
    repository = MemorySandboxRepository()
    record = SandboxRecord(
        SandboxRef("sb-ready", "provider-sb-ready", Endpoint("http://ready:8080")),
        SandboxState.READY,
    )
    await repository.save(record)
    provider = ReconcileProvider([discovered_sandbox("sb-ready")])
    reconciler = SandboxStartupReconciler(repository, provider)

    result = await reconciler.reconcile()

    assert result.matched_ready == 1
    assert provider.health_checks == ["sb-ready"]
    assert provider.destroyed == []
    assert (await repository.get("sb-ready")).state == SandboxState.READY


@pytest.mark.asyncio
async def test_startup_reconciler_marks_missing_ready_record_lost() -> None:
    repository = MemorySandboxRepository()
    await repository.save(
        SandboxRecord(SandboxRef("sb-missing", "provider-missing"), SandboxState.READY)
    )
    provider = ReconcileProvider([])
    reconciler = SandboxStartupReconciler(repository, provider)

    result = await reconciler.reconcile()

    assert result.missing_marked_lost == 1
    assert (await repository.get("sb-missing")).state == SandboxState.LOST


@pytest.mark.asyncio
async def test_startup_reconciler_destroys_inflight_warmup_container() -> None:
    repository = MemorySandboxRepository()
    await repository.save(
        SandboxRecord(SandboxRef("sb-warming", "provider-warming"), SandboxState.WARMING)
    )
    provider = ReconcileProvider([discovered_sandbox("sb-warming")])
    reconciler = SandboxStartupReconciler(repository, provider)

    result = await reconciler.reconcile()

    assert result.inflight_destroyed == 1
    assert provider.destroyed == [("sb-warming", "startup_inflight")]
    assert (await repository.get("sb-warming")).state == SandboxState.DESTROYED


@pytest.mark.asyncio
async def test_startup_reconciler_finishes_destroying_record() -> None:
    repository = MemorySandboxRepository()
    await repository.save(
        SandboxRecord(
            SandboxRef("sb-destroying", "provider-destroying"),
            SandboxState.DESTROYING,
        )
    )
    provider = ReconcileProvider([discovered_sandbox("sb-destroying")])
    reconciler = SandboxStartupReconciler(repository, provider)

    result = await reconciler.reconcile()

    assert result.destroying_finished == 1
    assert provider.destroyed == [("sb-destroying", "startup_destroying")]
    assert (await repository.get("sb-destroying")).state == SandboxState.DESTROYED


@pytest.mark.asyncio
async def test_startup_reconciler_does_not_destroy_authoritative_user_record() -> None:
    repository = MemorySandboxRepository()
    await repository.save(
        SandboxRecord(
            SandboxRef("sb-user", "provider-user"),
            SandboxState.USER_ACTIVE,
            owner_user_id="user-1",
        )
    )
    provider = ReconcileProvider([discovered_sandbox("sb-user")])
    reconciler = SandboxStartupReconciler(repository, provider)

    result = await reconciler.reconcile()

    assert result.orphan_destroyed == 0
    assert provider.destroyed == []
    assert (await repository.get("sb-user")).state == SandboxState.USER_ACTIVE


def test_container_pool_startup_reconciler_uses_repository_and_provider() -> None:
    from sandbox.container import Container

    container = Container()
    repository = MemorySandboxRepository()
    provider = ReconcileProvider([])
    container.repository.override(repository)
    container.provider.override(provider)

    reconciler = container.startup_reconciler()

    assert reconciler._repository is repository
    assert reconciler._provider is provider


@pytest.mark.asyncio
async def test_watcher_fills_ready_deficit() -> None:
    provider = FakeProvider()
    repository = MemorySandboxRepository()
    pool = SandboxPool(repository, min_ready=1, target_ready=2)
    watcher = Watcher(
        pool, repository, provider, SandboxSpec("test"), target_ready=2, min_ready=1
    )
    assert await watcher.reconcile() == 2
    assert (await pool.snapshot()).counts[SandboxState.READY] == 2
