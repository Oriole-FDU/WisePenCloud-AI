"""P0/P1 外部依赖装配：Mongo 客户端、仓储和 application 用例。"""

from dependency_injector import containers, providers
from pymongo import AsyncMongoClient

from rag_v3.application.document import DocumentPreparer
from rag_v3.application.publication import AclSynchronizer, DocumentPublication
from rag_v3.application.snapshot import ActiveDocumentSnapshotLoader
from rag_v3.core.config.app_settings import settings
from rag_v3.core.persistence.mongo import (
    MongoAuthoritativeAclReader,
    MongoDocChunkRepository,
    MongoDocumentRepository,
    MongoResourceAclRepository,
    MongoResourceIndexStateRepository,
)


def _resource_items_collection(client: AsyncMongoClient):
    return client[settings.resource_mongodb_db_name]["wispen_resource_items"]


class Container(containers.DeclarativeContainer):
    """集中管理当前已落地的 Mongo 生命周期和 application 用例。"""

    mongo_client = providers.Singleton(AsyncMongoClient, settings.MONGODB_URL)
    resource_items_collection = providers.Factory(
        _resource_items_collection,
        client=mongo_client,
    )

    documents = providers.Singleton(MongoDocumentRepository)
    doc_chunks = providers.Singleton(MongoDocChunkRepository)
    index_states = providers.Singleton(MongoResourceIndexStateRepository)
    resource_acls = providers.Singleton(MongoResourceAclRepository)
    authoritative_acls = providers.Singleton(
        MongoAuthoritativeAclReader,
        collection=resource_items_collection,
    )

    document_publication = providers.Factory(
        DocumentPublication,
        documents=documents,
        index_states=index_states,
    )
    document_preparer = providers.Factory(
        DocumentPreparer,
        publication=document_publication,
        doc_chunks=doc_chunks,
    )
    acl_synchronizer = providers.Factory(
        AclSynchronizer,
        authoritative_reader=authoritative_acls,
        local_repository=resource_acls,
    )
    active_document_snapshots = providers.Factory(
        ActiveDocumentSnapshotLoader,
        documents=documents,
        index_states=index_states,
        resource_acls=resource_acls,
    )


container = Container()
