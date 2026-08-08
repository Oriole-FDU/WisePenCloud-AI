from __future__ import annotations

from dependency_injector import containers, providers

from sandbox_v1.application.services.sandbox_pool import SandboxPool
from sandbox_v1.application.services.sandbox_startup_reconciler import (
    SandboxStartupReconciler,
)
from sandbox_v1.application.services.sandbox_watcher import Watcher
from sandbox_v1.application.services.workspace_eviction import WorkspaceEvictionWorker
from sandbox_v1.application.services.workspace_service import WorkspaceService
from sandbox_v1.core.observability import MetricsCollector
from sandbox_v1.core.storage.filesystem import LocalWorkspaceSnapshotCache
from sandbox_v1.core.storage.mongo import (
    MongoSandboxRepository,
    MongoWorkspaceRepository,
)
from sandbox_v1.domain.entities import SandboxSpec


def _sandbox_spec(image: str) -> SandboxSpec:
    """构造 watcher 补池时使用的 provider-neutral 创建规格。"""

    return SandboxSpec(image=image)


def _mongo_client(url: str):
    """延迟构造异步 Mongo client，避免导入阶段强依赖 pymongo。"""

    from pymongo import AsyncMongoClient

    return AsyncMongoClient(url)


def _mongo_database(client, database_name: str):
    """从异步 Mongo client 取得配置指定的 database。"""

    return client[database_name]


class Container(containers.DeclarativeContainer):
    """沙箱池核心的依赖注入图。

    Repository、Pool、StartupReconciler 和 Watcher 属于本服务核心；具体
    Docker/AIO runtime provider 由部署层注入，避免核心服务绑定某个运行时实现。
    """

    # 运行时配置由 main.py 从 AppSettings 注入。
    config = providers.Configuration()

    # 指标在进程内共享；Mongo client/database 是所有持久化 Repository 的依赖。
    metrics = providers.Singleton(MetricsCollector)
    mongo_client = providers.Singleton(_mongo_client, url=config.MONGODB_URL)
    mongo_database = providers.Singleton(
        _mongo_database,
        client=mongo_client,
        database_name=config.MONGODB_DB_NAME,
    )

    # Mongo Repository 取代旧内存实现，跨进程/重启持久化 sandbox 和 workspace 状态。
    repository = providers.Singleton(
        MongoSandboxRepository,
        database=mongo_database,
        metrics=metrics,
    )
    workspace_repository = providers.Singleton(
        MongoWorkspaceRepository,
        database=mongo_database,
    )

    # WorkspaceService 组合 Mongo 生命周期记录和本地文件系统快照缓存。
    workspace_cache = providers.Singleton(
        LocalWorkspaceSnapshotCache,
        cache_root=config.SANDBOX_WORKSPACE_CACHE_ROOT,
        ttl_seconds=config.SANDBOX_WORKSPACE_SNAPSHOT_TTL_SECONDS,
        max_bytes=config.SANDBOX_WORKSPACE_CACHE_MAX_BYTES,
        high_watermark_ratio=config.SANDBOX_WORKSPACE_CACHE_HIGH_WATERMARK_RATIO,
        target_watermark_ratio=config.SANDBOX_WORKSPACE_CACHE_TARGET_WATERMARK_RATIO,
    )
    workspace_service = providers.Singleton(
        WorkspaceService,
        repository=workspace_repository,
        cache=workspace_cache,
        workspace_root=config.SANDBOX_WORKSPACE_ROOT,
        metrics=metrics,
    )

    # 快照淘汰 worker 只依赖 service，按配置周期执行 TTL/LRU 清理。
    workspace_eviction_worker = providers.Singleton(
        WorkspaceEvictionWorker,
        workspace_service=workspace_service,
        interval_seconds=config.SANDBOX_WORKSPACE_EVICTION_INTERVAL_SECONDS,
    )

    # Pool 是用户消费和容量计划门面，不直接创建或销毁 runtime 容器。
    pool = providers.Singleton(
        SandboxPool,
        repository=repository,
        min_ready=config.SANDBOX_MIN_READY,
        target_ready=config.SANDBOX_TARGET_READY,
        max_user_bindings=config.SANDBOX_MAX_USER_BINDINGS,
    )

    # 部署层必须用具体容器运行时覆盖该端口。
    provider = providers.Dependency()

    # 启动对账负责把 provider 发现容器与 Repository 权威记录重新对齐。
    startup_reconciler = providers.Singleton(
        SandboxStartupReconciler,
        repository=repository,
        provider=provider,
    )

    # Watcher 按 Pool 计划补池，并处理 warmup、失败清理和 stale 回收。
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
