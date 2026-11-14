from __future__ import annotations

from dependency_injector import containers, providers
from v2.nacos import NacosNamingService

from common.cloud.service_discovery import ServiceDiscovery
from common.http.rpc_client import RpcClient

from sandbox.application import CommandTaskService, ContainerManager, ImageBuilder, SandboxFileService, Watcher, WorkspaceManager
from sandbox.core.config.app_settings import settings
from sandbox.core.config.bootstrap_settings import bootstrap_settings
from sandbox.core.config.nacos import nacos_client_manager
from sandbox.core.providers import AIOAdapter, SandboxProviderManager
from sandbox.core.storage.mongo import (
    MongoCommandTaskRepository,
    MongoSandboxRepository,
    MongoWorkspaceRepository,
)
from sandbox.service_client import DocumentServiceClient, FileStorageClient, WebAppServiceClient


def _mongo_client(url: str):
    from pymongo import AsyncMongoClient

    return AsyncMongoClient(url, tz_aware=True)


async def _provide_nacos_naming() -> NacosNamingService:
    return await nacos_client_manager.get_naming_client()


class Container(containers.DeclarativeContainer):
    """Sandbox dependency injection graph."""

    config = providers.Configuration()

    mongo_client = providers.Singleton(_mongo_client, url=config.persistence.mongodb.url)
    sandbox_repository = providers.Singleton(MongoSandboxRepository)
    command_task_repository = providers.Singleton(MongoCommandTaskRepository)
    workspace_repository = providers.Singleton(MongoWorkspaceRepository)

    service_discovery = providers.Singleton(
        ServiceDiscovery,
        naming_client_provider=providers.Object(_provide_nacos_naming),
        group_name=bootstrap_settings.NACOS_GROUP,
        default_strategy=config.rpc.lb_strategy,
        cache_ttl_seconds=config.service_discovery.cache_ttl_seconds,
    )
    rpc_client = providers.Singleton(
        RpcClient,
        discovery=service_discovery,
        from_source_secret=config.security.from_source_secret,
        timeout=config.rpc.default_timeout_seconds,
        retries=config.rpc.default_retries,
        default_strategy=config.rpc.lb_strategy,
    )
    file_storage_client = providers.Singleton(
        FileStorageClient,
        rpc=rpc_client,
    )
    document_service_client = providers.Singleton(
        DocumentServiceClient,
        rpc=rpc_client,
    )
    web_app_service_client = providers.Singleton(
        WebAppServiceClient,
        rpc=rpc_client,
    )

    sandbox_provider_manager = providers.Singleton(
        SandboxProviderManager,
        provider_classes=[AIOAdapter],
        provider_settings={
            provider_id: provider_settings.model_dump(by_alias=False)
            for provider_id, provider_settings in settings.provider.settings.items()
        },
    )
    container_manager = providers.Singleton(
        ContainerManager
    )
    image_builder = providers.Singleton(
        ImageBuilder,
        container_manager=container_manager,
    )
    workspace_manager = providers.Singleton(
        WorkspaceManager,
        sandbox_repository=sandbox_repository,
        workspace_repository=workspace_repository,
        container_manager=container_manager,
    )
    command_task_service = providers.Singleton(
        CommandTaskService,
        task_repository=command_task_repository,
        sandbox_repository=sandbox_repository,
        workspace_repository=workspace_repository,
        workspace_manager=workspace_manager,
        provider_manager=sandbox_provider_manager,
    )
    file_service = providers.Singleton(
        SandboxFileService,
        sandbox_repository=sandbox_repository,
        workspace_manager=workspace_manager,
        provider_manager=sandbox_provider_manager,
        file_storage_client=file_storage_client,
        document_service_client=document_service_client,
        web_app_service_client=web_app_service_client,
    )
    watcher = providers.Singleton(
        Watcher,
        sandbox_repository=sandbox_repository,
        sandbox_provider_manager=sandbox_provider_manager,
        container_manager=container_manager,
        workspace_manager=workspace_manager,
        image_builder=image_builder,
    )


container = Container()
