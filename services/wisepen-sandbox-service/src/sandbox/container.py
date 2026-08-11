from __future__ import annotations

from dependency_injector import containers, providers

from sandbox.application import ContainerManager, Watcher
from sandbox.core.providers import AIOAdapter
from sandbox.core.storage.mongo import (
    MongoSandboxRepository,
    MongoWorkspaceRepository,
)


def _mongo_client(url: str):
    from pymongo import AsyncMongoClient

    return AsyncMongoClient(url)


class Container(containers.DeclarativeContainer):
    """Sandbox dependency injection graph."""

    config = providers.Configuration()

    mongo_client = providers.Singleton(_mongo_client, url=config.MONGODB_URL)
    sandbox_repository = providers.Singleton(MongoSandboxRepository)
    workspace_repository = providers.Singleton(MongoWorkspaceRepository)

    sandbox_provider = providers.Singleton(
        AIOAdapter,
        sandbox_image=config.SANDBOX_IMAGE,
        health_timeout_seconds=config.SANDBOX_AIO_HEALTH_TIMEOUT_SECONDS,
    )
    container_manager = providers.Singleton(
        ContainerManager,
        endpoint_host=config.SANDBOX_DOCKER_ENDPOINT_HOST,
    )
    watcher = providers.Singleton(
        Watcher,
        sandbox_repository=sandbox_repository,
        sandbox_provider=sandbox_provider,
        container_manager=container_manager
    )


container = Container()
