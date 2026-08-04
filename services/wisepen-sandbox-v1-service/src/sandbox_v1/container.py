from __future__ import annotations

from dependency_injector import containers, providers

from sandbox_v1.application.services.sandbox_pool import SandboxPool
from sandbox_v1.application.services.sandbox_startup_reconciler import (
    SandboxStartupReconciler,
)
from sandbox_v1.application.services.sandbox_watcher import Watcher
from sandbox_v1.core.observability import MetricsCollector
from sandbox_v1.core.storage.memory import MemorySandboxRepository
from sandbox_v1.domain.entities import SandboxSpec


def _sandbox_spec(image: str) -> SandboxSpec:
    """Build the provider-neutral spec used by the pool replenisher."""
    return SandboxSpec(image=image)


class Container(containers.DeclarativeContainer):
    """Dependency graph for the container-pool core.

    The concrete runtime provider is deliberately a dependency. Docker/AIO
    selection belongs to a later integration layer and is not part of this
    service's core implementation.
    """

    config = providers.Configuration()

    metrics = providers.Singleton(MetricsCollector)
    repository = providers.Singleton(MemorySandboxRepository, metrics=metrics)
    pool = providers.Singleton(
        SandboxPool,
        repository=repository,
        min_ready=config.SANDBOX_MIN_READY,
        target_ready=config.SANDBOX_TARGET_READY,
        max_user_bindings=config.SANDBOX_MAX_USER_BINDINGS,
    )

    # Deployment code must override this port with its container runtime.
    provider = providers.Dependency()
    startup_reconciler = providers.Singleton(
        SandboxStartupReconciler,
        repository=repository,
        provider=provider,
    )
    watcher = providers.Singleton(
        Watcher,
        pool=pool,
        repository=repository,
        provider=provider,
        spec=providers.Factory(_sandbox_spec, image=config.SANDBOX_IMAGE),
        min_ready=config.SANDBOX_MIN_READY,
        reserve=config.SANDBOX_READY_RESERVE,
        max_create_batch=config.SANDBOX_MAX_CREATE_BATCH,
        warmup_timeout_seconds=config.SANDBOX_WARMUP_TIMEOUT_SECONDS,
        destroy_timeout_seconds=config.SANDBOX_DESTROY_TIMEOUT_SECONDS,
        interval_seconds=config.SANDBOX_WATCHER_INTERVAL_SECONDS,
        warmup_max_retries=config.SANDBOX_WARMUP_MAX_RETRIES,
        warmup_retry_backoff_seconds=config.SANDBOX_WARMUP_RETRY_BACKOFF_SECONDS,
        warmup_retry_max_backoff_seconds=config.SANDBOX_WARMUP_RETRY_MAX_BACKOFF_SECONDS,
        metrics=metrics,
    )


container = Container()
