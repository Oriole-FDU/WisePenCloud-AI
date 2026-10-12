"""ACL 阶段的依赖装配。"""

from dependency_injector import containers, providers
from pymongo import AsyncMongoClient

from rag.application.acl.synchronizer import AclEventConsumer, AclSynchronizer
from rag.backends.mongo.acl import MongoResourceAclRepository
from rag.core.config.app_settings import settings


def _resource_items_collection(client: AsyncMongoClient):
    return client[settings.resource_mongodb_db_name]["wispen_resource_items"]


def _acl_collection(client: AsyncMongoClient):
    return client[settings.MONGODB_DB_NAME]["resource_acls"]


class Container(containers.DeclarativeContainer):
    mongo_client = providers.Singleton(AsyncMongoClient, settings.MONGODB_URL)
    resource_items_collection = providers.Factory(
        _resource_items_collection,
        client=mongo_client,
    )
    acl_collection = providers.Factory(_acl_collection, client=mongo_client)
    acl_repository = providers.Singleton(
        MongoResourceAclRepository,
        collection=acl_collection,
    )
    acl_synchronizer = providers.Factory(
        AclSynchronizer,
        collection=resource_items_collection,
        repository=acl_repository,
    )
    acl_consumer = providers.Factory(
        AclEventConsumer,
        projection=acl_synchronizer,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        topic=settings.KAFKA_RESOURCE_ACL_RECALC_TOPIC,
        group_id=settings.KAFKA_RAG_ACL_RECALC_GROUP_ID,
        max_poll_interval_ms=settings.KAFKA_MAX_POLL_INTERVAL_MS,
    )


container = Container()
