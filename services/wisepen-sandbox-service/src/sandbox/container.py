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
    # 约定格式为 module:Class，Class 提供 from_environment() 构造方法。
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
        # 工作区存储与 Provider 解耦：本地开发用文件系统，生产可替换为对象存储实现。
        LocalWorkspaceStore(
            settings.SANDBOX_WORKSPACE_ROOT,
            max_files=settings.SANDBOX_WORKSPACE_CACHE_MAX_FILES,
            max_file_bytes=settings.SANDBOX_WORKSPACE_CACHE_MAX_FILE_BYTES,
            max_total_bytes=settings.SANDBOX_WORKSPACE_CACHE_MAX_TOTAL_BYTES,
            manifest_name=settings.SANDBOX_WORKSPACE_CACHE_MANIFEST_NAME,
        ),
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
