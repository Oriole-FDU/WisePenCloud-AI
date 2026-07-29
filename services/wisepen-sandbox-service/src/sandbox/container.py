from __future__ import annotations

import importlib

from dependency_injector import containers, providers

from sandbox.application.services.sandbox_pool import SandboxPool
from sandbox.application.services.sandbox_scheduler import SandboxScheduler
from sandbox.application.services.sandbox_watcher import Watcher
from sandbox.core.observability import MetricsCollector
from sandbox.core.storage.local import LocalWorkspaceStore
from sandbox.core.storage.memory import MemoryLeaderLease, MemorySandboxRepository
from sandbox.domain.entities import SandboxSpec


def _load_provider(target: str) -> object:
    if not target:
        raise RuntimeError("SANDBOX_PROVIDER_FACTORY 必须指向 SandboxProvider 工厂")
    # 约定格式为 module:Class，Class 提供 from_environment() 构造方法。
    module_name, factory_name = target.split(":", 1)
    factory = getattr(importlib.import_module(module_name), factory_name)
    return factory.from_environment()


class Container(containers.DeclarativeContainer):
    """Sandbox 运行时依赖容器，与 API endpoints 共用同一组服务实例。"""

    config = providers.Configuration()

    metrics = providers.Singleton(MetricsCollector)
    repository = providers.Singleton(MemorySandboxRepository, metrics=metrics)
    pool = providers.Singleton(
        SandboxPool,
        repository=repository,
        lease_ttl_seconds=config.SANDBOX_LEASE_TTL_SECONDS,
        min_ready=config.SANDBOX_MIN_READY,
        target_ready=config.SANDBOX_TARGET_READY,
    )
    provider = providers.Singleton(_load_provider, target=config.SANDBOX_PROVIDER_FACTORY)
    workspace_store = providers.Singleton(
        LocalWorkspaceStore,
        root=config.SANDBOX_WORKSPACE_ROOT,
        max_files=config.SANDBOX_WORKSPACE_CACHE_MAX_FILES,
        max_file_bytes=config.SANDBOX_WORKSPACE_CACHE_MAX_FILE_BYTES,
        max_total_bytes=config.SANDBOX_WORKSPACE_CACHE_MAX_TOTAL_BYTES,
        manifest_name=config.SANDBOX_WORKSPACE_CACHE_MANIFEST_NAME,
    )
    scheduler = providers.Singleton(
        SandboxScheduler,
        pool=pool,
        repository=repository,
        provider=provider,
        workspace_store=workspace_store,
        destroy_timeout_seconds=config.SANDBOX_DESTROY_TIMEOUT_SECONDS,
        destroy_max_retries=3,
        metrics=metrics,
    )
    leader_lease = providers.Singleton(MemoryLeaderLease)
    watcher = providers.Singleton(
        Watcher,
        pool=pool,
        repository=repository,
        provider=provider,
        spec=providers.Factory(SandboxSpec, image=config.SANDBOX_IMAGE),
        scheduler=scheduler,
        leader_lease=leader_lease,
        target_ready=config.SANDBOX_TARGET_READY,
        min_ready=config.SANDBOX_MIN_READY,
        reserve=config.SANDBOX_READY_RESERVE,
        max_create_batch=config.SANDBOX_MAX_CREATE_BATCH,
        warmup_timeout_seconds=config.SANDBOX_WARMUP_TIMEOUT_SECONDS,
        destroy_timeout_seconds=config.SANDBOX_DESTROY_TIMEOUT_SECONDS,
        max_retries=config.SANDBOX_WARMUP_MAX_RETRIES,
        metrics=metrics,
    )


container = Container()
