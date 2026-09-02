"""P0/P1 外部依赖装配：Mongo 客户端、仓储和 application 用例。"""

from common.utils.ranking import RankingPipeline
from common.utils.ranking.rank_gates import (
    HighLowRelevanceGate,
    HighLowRelevanceGateConfig,
)
from common.utils.ranking.rerankers import (
    ZeroEntropyReranker,
    ZeroEntropyRerankerConfig,
)
from dependency_injector import containers, providers
from neo4j import AsyncGraphDatabase
from openai import AsyncOpenAI
from pymongo import AsyncMongoClient
from qdrant_client import AsyncQdrantClient
from zeroentropy import AsyncZeroEntropy

from rag.application.document.indexing import DocumentIndexBuilder
from rag.application.document.models import (
    DocChunkMetadataRegistry,
    DocumentMetadataRegistry,
)
from rag.application.document.preparation import DocumentPreparer
from rag.application.events import (
    AclRecalculateHandler,
    DocumentReadyHandler,
    ResourceDestroyHandler,
)
from rag.application.graph.graph_fact_builder import GraphFactBuilder
from rag.application.graph.graph_indexing import GraphIndexBuilder
from rag.application.outline import OutlineBuilder
from rag.application.publication import AclSynchronizer, DocumentPublication
from rag.application.reading import DocumentReader
from rag.application.retrieval.graph_retriever import GraphRetriever
from rag.application.retrieval.hybrid_retriever import HybridRetriever
from rag.application.snapshot import ActiveDocumentSnapshotLoader
from rag.core.config.app_settings import settings
from rag.core.persistence.mongo import (
    MongoAuthoritativeAclReader,
    MongoDocChunkRepository,
    MongoDocumentRepository,
    MongoGraphFactRepository,
    MongoResourceAclRepository,
    MongoResourceIndexStateRepository,
)
from rag.core.persistence.neo4j import Neo4jGraphTopologyRepository
from rag.core.persistence.qdrant import (
    QdrantDocumentVectorRepository,
    QdrantGraphEdgeVectorRepository,
    QdrantGraphNodeVectorRepository,
)


def _resource_items_collection(client: AsyncMongoClient):
    return client[settings.resource_mongodb_db_name]["wispen_resource_items"]


def _build_ranking_pipeline(
    zero_entropy_client: AsyncZeroEntropy,
) -> RankingPipeline:
    """V3 只保留精排和相关性门控；两路 Qdrant 结果不在此处融合。"""
    return RankingPipeline(
        reranker=ZeroEntropyReranker(
            client=zero_entropy_client,
            config=ZeroEntropyRerankerConfig(model=settings.RERANKER_MODEL),
        ),
        gate=HighLowRelevanceGate(
            HighLowRelevanceGateConfig(
                low_watermark=settings.RAG_RERANK_RELEVANCE_LOW_WATERMARK,
                high_watermark=settings.RAG_RERANK_RELEVANCE_HIGH_WATERMARK,
                uncertain_limit=settings.RAG_RERANK_UNCERTAIN_LIMIT,
            )
        ),
    )


def _build_metadata_registry(plugins):
    """在容器解析插件列表后收集 metadata 类型，避免声明期读取 provider。"""
    return DocumentMetadataRegistry(
        metadata_types=[plugin.metadata_type for plugin in plugins]
    )


class Container(containers.DeclarativeContainer):
    """集中管理当前已落地的 Mongo 生命周期和 application 用例。"""

    mongo_client = providers.Singleton(AsyncMongoClient, settings.MONGODB_URL)
    resource_items_collection = providers.Factory(
        _resource_items_collection,
        client=mongo_client,
    )

    # 默认没有通用 Ontology；部署垂类时由 composition 显式覆盖此列表。
    graph_plugins = providers.List()
    metadata_registry = providers.Singleton(
        _build_metadata_registry,
        plugins=graph_plugins,
    )
    documents = providers.Singleton(
        MongoDocumentRepository,
        metadata_registry=metadata_registry,
    )
    chunk_metadata_registry = providers.Singleton(DocChunkMetadataRegistry)
    doc_chunks = providers.Singleton(
        MongoDocChunkRepository,
        metadata_registry=chunk_metadata_registry,
    )
    index_states = providers.Singleton(MongoResourceIndexStateRepository)
    resource_acls = providers.Singleton(MongoResourceAclRepository)
    authoritative_acls = providers.Singleton(
        MongoAuthoritativeAclReader,
        collection=resource_items_collection,
    )
    openai_client = providers.Singleton(
        AsyncOpenAI,
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
    )
    zero_entropy_client = providers.Singleton(
        AsyncZeroEntropy,
        api_key=settings.ZERO_ENTROPY_API_KEY,
    )
    hybrid_ranking_pipeline = providers.Singleton(
        _build_ranking_pipeline,
        zero_entropy_client=zero_entropy_client,
    )
    qdrant_client = providers.Singleton(
        AsyncQdrantClient,
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
        api_key=settings.QDRANT_PASSWORD or None,
    )
    document_vectors = providers.Singleton(
        QdrantDocumentVectorRepository,
        client=qdrant_client,
        collection_name=settings.QDRANT_DOCUMENT_CHUNK_COLLECTION_NAME,
        dense_vector_size=settings.EMBEDDING_DIMENSIONS,
        dense_vector_name=settings.QDRANT_DOCUMENT_DENSE_VECTOR_NAME,
        sparse_vector_name=settings.QDRANT_DOCUMENT_SPARSE_VECTOR_NAME,
    )
    graph_node_vectors = providers.Singleton(
        QdrantGraphNodeVectorRepository,
        client=qdrant_client,
        collection_name=settings.QDRANT_GRAPH_NODE_COLLECTION_NAME,
        dense_vector_size=settings.EMBEDDING_DIMENSIONS,
        dense_vector_name=settings.QDRANT_GRAPH_NODE_DENSE_VECTOR_NAME,
    )
    graph_edge_vectors = providers.Singleton(
        QdrantGraphEdgeVectorRepository,
        client=qdrant_client,
        collection_name=settings.QDRANT_GRAPH_EDGE_COLLECTION_NAME,
        dense_vector_size=settings.EMBEDDING_DIMENSIONS,
        dense_vector_name=settings.QDRANT_GRAPH_EDGE_DENSE_VECTOR_NAME,
        sparse_vector_name=settings.QDRANT_GRAPH_EDGE_SPARSE_VECTOR_NAME,
    )
    if settings.GRAPH_ENABLED:
        neo4j_driver = providers.Singleton(
            AsyncGraphDatabase.driver,
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
        )
        graph_topology = providers.Singleton(
            Neo4jGraphTopologyRepository,
            driver=neo4j_driver,
        )
    else:
        neo4j_driver = providers.Object(None)
        graph_topology = providers.Object(None)

    document_publication = providers.Factory(
        DocumentPublication,
        documents=documents,
        doc_chunks=doc_chunks,
        document_vectors=document_vectors,
        index_states=index_states,
    )
    document_preparer = providers.Factory(
        DocumentPreparer,
        publication=document_publication,
        doc_chunks=doc_chunks,
    )
    document_index_builder = providers.Factory(
        DocumentIndexBuilder,
        documents=documents,
        doc_chunks=doc_chunks,
        resource_acls=resource_acls,
        index_states=index_states,
        publication=document_publication,
        document_vectors=document_vectors,
        openai_client=openai_client,
        query_model=settings.QUERY_MODEL,
        embedding_model=settings.EMBEDDING_MODEL,
        embedding_dimensions=settings.EMBEDDING_DIMENSIONS,
        max_concurrency=settings.DOCUMENT_ENHANCEMENT_MAX_CONCURRENCY,
        enhancement_enabled=settings.DOCUMENT_ENHANCEMENT_ENABLED,
    )
    document_ready_handler = providers.Factory(
        DocumentReadyHandler,
        preparer=document_preparer,
        index_builder=document_index_builder,
    )
    acl_synchronizer = providers.Factory(
        AclSynchronizer,
        authoritative_reader=authoritative_acls,
        local_repository=resource_acls,
    )
    acl_recalculate_handler = providers.Factory(
        AclRecalculateHandler,
        synchronizer=acl_synchronizer,
    )
    resource_destroy_handler = providers.Factory(
        ResourceDestroyHandler,
        publication=document_publication,
        document_vectors=document_vectors,
        node_vectors=graph_node_vectors,
        edge_vectors=graph_edge_vectors,
        topology=graph_topology,
        graph_enabled=settings.GRAPH_ENABLED,
    )
    active_document_snapshots = providers.Factory(
        ActiveDocumentSnapshotLoader,
        documents=documents,
        index_states=index_states,
        resource_acls=resource_acls,
    )
    document_reader = providers.Factory(
        DocumentReader,
        snapshots=active_document_snapshots,
    )
    outline_builder = providers.Factory(
        OutlineBuilder,
        snapshots=active_document_snapshots,
    )
    graph_facts = providers.Singleton(MongoGraphFactRepository)
    graph_fact_builder = providers.Factory(
        GraphFactBuilder,
        enabled=settings.GRAPH_ENABLED,
        documents=documents,
        doc_chunks=doc_chunks,
        graph_facts=graph_facts,
        index_states=index_states,
        plugins=graph_plugins,
        openai_client=openai_client,
        query_model=settings.QUERY_MODEL,
        max_concurrency=settings.DOCUMENT_ENHANCEMENT_MAX_CONCURRENCY,
    )
    graph_index_builder = providers.Factory(
        GraphIndexBuilder,
        enabled=settings.GRAPH_ENABLED,
        documents=documents,
        doc_chunks=doc_chunks,
        graph_facts=graph_facts,
        resource_acls=resource_acls,
        index_states=index_states,
        topology=graph_topology,
        node_vectors=graph_node_vectors,
        edge_vectors=graph_edge_vectors,
        openai_client=openai_client,
        embedding_model=settings.EMBEDDING_MODEL,
        embedding_dimensions=settings.EMBEDDING_DIMENSIONS,
    )
    hybrid_retriever = providers.Factory(
        HybridRetriever,
        documents=documents,
        doc_chunks=doc_chunks,
        document_vectors=document_vectors,
        index_states=index_states,
        resource_acls=resource_acls,
        ranking_pipeline=hybrid_ranking_pipeline,
        openai_client=openai_client,
        embedding_model=settings.EMBEDDING_MODEL,
        embedding_dimensions=settings.EMBEDDING_DIMENSIONS,
    )
    graph_retriever = providers.Factory(
        GraphRetriever,
        enabled=settings.GRAPH_ENABLED,
        topology=graph_topology,
        node_vectors=graph_node_vectors,
        edge_vectors=graph_edge_vectors,
        graph_facts=graph_facts,
        doc_chunks=doc_chunks,
        documents=documents,
        index_states=index_states,
        resource_acls=resource_acls,
        ranking_pipeline=hybrid_ranking_pipeline,
        plugins=graph_plugins,
        openai_client=openai_client,
        embedding_model=settings.EMBEDDING_MODEL,
        embedding_dimensions=settings.EMBEDDING_DIMENSIONS,
    )


container = Container()
