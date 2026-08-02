from __future__ import annotations

import importlib

from dependency_injector import containers, providers

from sandbox.application.services.sandbox_pool import SandboxPool
from sandbox.application.services.sandbox_scheduler import SandboxScheduler
from sandbox.application.services.sandbox_watcher import Watcher
from sandbox.core.observability import MetricsCollector
from sandbox.core.providers.aio_adapter.file_transfer import DockerWorkspaceTransfer
from sandbox.core.providers.aio_adapter.models import AdapterConfig
from sandbox.core.storage.local import LocalWorkspaceStore
from sandbox.core.storage.memory import MemoryLeaderLease, MemorySandboxRepository
from sandbox.domain.entities import SandboxSpec
from sandbox.domain.interfaces.file_transfer import FileTransferPort


def _load_provider(
    target: str,
    file_transfer: FileTransferPort,
    settings: AdapterConfig,
) -> object:
    if not target or target.count(":") != 1:
        raise RuntimeError("SANDBOX_PROVIDER_FACTORY 必须指向 SandboxProvider 工厂")
    # 约定格式为 module:Class，Class 只接收已通过 AppSettings 校验的配置。
    module_name, factory_name = target.split(":", 1)
    if not module_name or not factory_name:
        raise RuntimeError("SANDBOX_PROVIDER_FACTORY 必须指向 SandboxProvider 工厂")
    factory = getattr(importlib.import_module(module_name), factory_name)
    from_settings = getattr(factory, "from_settings", None)
    if not callable(from_settings):
        raise RuntimeError("SANDBOX_PROVIDER_FACTORY 工厂必须提供 from_settings 方法")
    return from_settings(settings, file_transfer)


def _workspace_store(
    backend: str,
    *,
    root: str,
    mongo_url: str,
    mongo_database: str,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
    manifest_name: str,
) -> object:
    if backend == "local":
        return LocalWorkspaceStore(
            root=root,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
            manifest_name=manifest_name,
        )
    if backend == "mongo":
        try:
            from sandbox.core.storage.mongo import MongoWorkspaceStore
        except ImportError as exc:
            raise RuntimeError(
                "Mongo workspace store requires the pymongo dependency"
            ) from exc
        return MongoWorkspaceStore(
            mongo_url=mongo_url,
            db_name=mongo_database,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
            max_total_bytes=max_total_bytes,
        )
    raise RuntimeError(f"Unsupported workspace store backend: {backend}")


def _sandbox_spec(image: str, browser_no_sandbox: str) -> SandboxSpec:
    environment = (
        {"BROWSER_NO_SANDBOX": browser_no_sandbox}
        if browser_no_sandbox
        else {}
    )
    return SandboxSpec(image=image, environment=environment)


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
    file_transfer = providers.Singleton(
        DockerWorkspaceTransfer,
        docker_bin=config.SANDBOX_DOCKER_BIN,
        workspace_root=config.SANDBOX_CONTAINER_WORKSPACE_ROOT,
        container_user=config.SANDBOX_CONTAINER_USER,
        command_timeout_seconds=config.SANDBOX_DOCKER_COMMAND_TIMEOUT_SECONDS,
        max_files=config.SANDBOX_WORKSPACE_CACHE_MAX_FILES,
        max_file_bytes=config.SANDBOX_WORKSPACE_CACHE_MAX_FILE_BYTES,
        max_total_bytes=config.SANDBOX_WORKSPACE_CACHE_MAX_TOTAL_BYTES,
    )
    provider_settings = providers.Factory(
        AdapterConfig,
        docker_bin=config.SANDBOX_DOCKER_BIN,
        image=config.SANDBOX_IMAGE,
        host=config.SANDBOX_DOCKER_HOST,
        api_port=config.SANDBOX_AIO_PORT,
        vnc_port=config.SANDBOX_VNC_PORT,
        network=config.SANDBOX_DOCKER_NETWORK,
        request_timeout_seconds=config.SANDBOX_REQUEST_TIMEOUT_SECONDS,
        execution_default_timeout_ms=config.SANDBOX_EXECUTION_DEFAULT_TIMEOUT_MS,
        execution_max_timeout_ms=config.SANDBOX_EXECUTION_MAX_TIMEOUT_MS,
        execution_transport_grace_seconds=config.SANDBOX_EXECUTION_TRANSPORT_GRACE_SECONDS,
        warmup_timeout_seconds=config.SANDBOX_WARMUP_TIMEOUT_SECONDS,
        health_timeout_seconds=config.SANDBOX_AIO_HEALTH_TIMEOUT_SECONDS,
        health_retry_interval_seconds=config.SANDBOX_AIO_HEALTH_RETRY_INTERVAL_SECONDS,
        command_timeout_seconds=config.SANDBOX_DOCKER_COMMAND_TIMEOUT_SECONDS,
        create_max_attempts=config.SANDBOX_DOCKER_CREATE_MAX_ATTEMPTS,
        create_retry_backoff_seconds=config.SANDBOX_DOCKER_CREATE_RETRY_BACKOFF_SECONDS,
        workdir=config.SANDBOX_AIO_WORKDIR,
        workspace_root=config.SANDBOX_CONTAINER_WORKSPACE_ROOT,
        tty=config.SANDBOX_DOCKER_TTY,
        owner_id=config.SANDBOX_OWNER_ID,
        browser_no_sandbox=config.SANDBOX_BROWSER_NO_SANDBOX,
        public_vnc_url_template=config.SANDBOX_PUBLIC_VNC_URL_TEMPLATE,
        public_websocket_url_template=config.SANDBOX_PUBLIC_WEBSOCKET_URL_TEMPLATE,
    )
    provider = providers.Singleton(
        _load_provider,
        target=config.SANDBOX_PROVIDER_FACTORY,
        file_transfer=file_transfer,
        settings=provider_settings,
    )
    workspace_store = providers.Singleton(
        _workspace_store,
        backend=config.SANDBOX_WORKSPACE_STORE_BACKEND,
        root=config.SANDBOX_WORKSPACE_ROOT,
        mongo_url=config.SANDBOX_MONGO_URL,
        mongo_database=config.SANDBOX_MONGO_DATABASE,
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
        destroy_max_retries=config.SANDBOX_DESTROY_MAX_RETRIES,
        destroy_backoff_seconds=config.SANDBOX_DESTROY_RETRY_BACKOFF_SECONDS,
        metrics=metrics,
    )
    leader_lease = providers.Singleton(MemoryLeaderLease)
    watcher = providers.Singleton(
        Watcher,
        pool=pool,
        repository=repository,
        provider=provider,
        spec=providers.Factory(
            _sandbox_spec,
            image=config.SANDBOX_IMAGE,
            browser_no_sandbox=config.SANDBOX_BROWSER_NO_SANDBOX,
        ),
        scheduler=scheduler,
        leader_lease=leader_lease,
        target_ready=config.SANDBOX_TARGET_READY,
        min_ready=config.SANDBOX_MIN_READY,
        reserve=config.SANDBOX_READY_RESERVE,
        max_create_batch=config.SANDBOX_MAX_CREATE_BATCH,
        warmup_timeout_seconds=config.SANDBOX_WARMUP_TIMEOUT_SECONDS,
        destroy_timeout_seconds=config.SANDBOX_DESTROY_TIMEOUT_SECONDS,
        interval_seconds=config.SANDBOX_WATCHER_INTERVAL_SECONDS,
        warmup_max_retries=config.SANDBOX_WARMUP_MAX_RETRIES,
        warmup_retry_backoff_seconds=config.SANDBOX_WARMUP_RETRY_BACKOFF_SECONDS,
        warmup_retry_max_backoff_seconds=config.SANDBOX_WARMUP_RETRY_MAX_BACKOFF_SECONDS,
        leader_lease_ttl_seconds=config.SANDBOX_LEADER_LEASE_TTL_SECONDS,
        leader_lease_renew_interval_seconds=config.SANDBOX_LEADER_LEASE_RENEW_INTERVAL_SECONDS,
        checkpoint_interval_seconds=config.SANDBOX_CHECKPOINT_INTERVAL_SECONDS,
        metrics=metrics,
    )


container = Container()
