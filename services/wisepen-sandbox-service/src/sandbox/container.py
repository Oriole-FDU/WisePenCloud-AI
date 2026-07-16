from __future__ import annotations

import importlib
from dataclasses import dataclass

from sandbox.application.services.sandbox_pool import SandboxPool
from sandbox.application.services.sandbox_scheduler import SandboxScheduler
from sandbox.application.services.sandbox_watcher import Watcher
from sandbox.core.storage.memory import MemoryLeaderLease, MemorySandboxRepository
from sandbox.core.observability import MetricsCollector
from sandbox.core.storage.local import LocalWorkspaceStore
from sandbox.core.config.app_settings import settings
from sandbox.domain.entities import SandboxSpec
from sandbox.domain.repositories import SandboxRepository


def _load_provider(target: str) -> object:
    if not target:
        raise RuntimeError("SANDBOX_PROVIDER_FACTORY 必须指向 SandboxProvider 工厂")
    module_name, factory_name = target.split(":", 1)
    factory = getattr(importlib.import_module(module_name), factory_name)
    return factory.from_environment()


@dataclass(frozen=True)
class SandboxContainer:
    repository: SandboxRepository
    pool: SandboxPool
    provider: object
    scheduler: SandboxScheduler
    watcher: Watcher


def build_container() -> SandboxContainer:
    metrics = MetricsCollector()
    repository = MemorySandboxRepository(metrics=metrics)
    pool = SandboxPool(
        repository,
        settings.SANDBOX_LEASE_TTL_SECONDS,
        min_ready=settings.SANDBOX_MIN_READY,
        target_ready=settings.SANDBOX_TARGET_READY,
    )
    provider = _load_provider(settings.SANDBOX_PROVIDER_FACTORY)
    scheduler = SandboxScheduler(
        pool,
        repository,
        provider,
        LocalWorkspaceStore(settings.SANDBOX_WORKSPACE_ROOT),
        destroy_timeout_seconds=settings.SANDBOX_DESTROY_TIMEOUT_SECONDS,
        destroy_max_retries=3,
        metrics=metrics,
    )
    watcher = Watcher(
        pool,
        repository,
        provider,
        SandboxSpec(image=settings.SANDBOX_IMAGE),
        scheduler=scheduler,
        leader_lease=MemoryLeaderLease(),
        target_ready=settings.SANDBOX_TARGET_READY,
        min_ready=settings.SANDBOX_MIN_READY,
        reserve=settings.SANDBOX_READY_RESERVE,
        max_create_batch=settings.SANDBOX_MAX_CREATE_BATCH,
        warmup_timeout_seconds=settings.SANDBOX_WARMUP_TIMEOUT_SECONDS,
        destroy_timeout_seconds=settings.SANDBOX_DESTROY_TIMEOUT_SECONDS,
        max_retries=settings.SANDBOX_WARMUP_MAX_RETRIES,
        metrics=metrics,
    )
    return SandboxContainer(repository, pool, provider, scheduler, watcher)
