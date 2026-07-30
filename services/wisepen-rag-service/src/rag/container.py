from dependency_injector import containers, providers
from neo4j import AsyncDriver, AsyncGraphDatabase
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models
import redis.asyncio as redis

from common.kafka import KafkaConsumerClient
from rag.application.rag.acl import RagAclProjector, RagPermissionAuthorizer
from rag.application.rag.evidence import RagEvidenceMaterializer
from rag.application.rag.graph_extraction import (
    KnowledgeGraphExtractor,
    QueryClientGraphRagLLM,
)
from rag.application.rag.graph_projection import KnowledgeGraphIndexer
from rag.application.rag.ingestion import (
    ContextIndexingService,
    RagContentIndexer,
    RagSectionProjector,
)
from rag.application.rag.kafka_consumers import (
    RagAclRecalculateConsumer,
    RagDocumentReadyConsumer,
    RagResourceDeletedConsumer,
)
from rag.application.rag.knowledge_navigation import KnowledgeNavigationService
from rag.application.rag.retrieval import (
    RagCandidateRetriever,
    RagPermissionFilterBuilder,
)
from rag.application.rag.section_navigation import RagSectionNavigator
from rag.core.config.app_settings import settings
from rag.core.persistence import (
    MongoRagAclProjectionRepository,
    MongoRagContentProjectionRepository,
    Neo4jKnowledgeGraphRepository,
    QdrantRagCandidateRepository,
    QdrantRagVectorIndexRepository,
    RedisKnowledgeGraphExtractionCache,
    RedisKnowledgeNavigationStateRepository,
    RedisRagContextIndexingCache,
)
from rag.utils.llm_clients import build_embedding_client, build_query_client
from rag.utils.ranking_presets import (
    KNOWLEDGE_GRAPH_PATH_PIPELINE,
    KNOWLEDGE_SEARCH_PIPELINE,
)


def _build_redis_client() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def _build_qdrant_client() -> AsyncQdrantClient:
    host = settings.QDRANT_HOST.strip()
    if not host:
        raise ValueError("QDRANT_HOST must not be empty")
    return AsyncQdrantClient(
        host=host,
        port=settings.QDRANT_PORT,
        api_key=settings.QDRANT_PASSWORD or None,
        https=False,
        cloud_inference=True,
        check_compatibility=False,
    )


def _build_neo4j_driver() -> AsyncDriver:
    return AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )


def _build_qdrant_bm25_config() -> qdrant_models.Bm25Config:
    return qdrant_models.Bm25Config(
        tokenizer=qdrant_models.TokenizerType(settings.QDRANT_RAG_BM25_TOKENIZER)
    )


def _build_rag_acl_kafka_consumer(
    consumer: RagAclRecalculateConsumer,
) -> KafkaConsumerClient:
    return KafkaConsumerClient(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        topic=settings.KAFKA_RESOURCE_ACL_RECALC_TOPIC,
        group_id=settings.KAFKA_RAG_ACL_RECALC_GROUP_ID,
        handler=consumer.handle,
    )


def _build_rag_document_kafka_consumer(
    consumer: RagDocumentReadyConsumer,
) -> KafkaConsumerClient:
    return KafkaConsumerClient(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        topic=settings.KAFKA_DOCUMENT_READY_TOPIC,
        group_id=settings.KAFKA_RAG_DOCUMENT_READY_GROUP_ID,
        handler=consumer.handle,
    )


def _build_rag_resource_deleted_kafka_consumer(
    consumer: RagResourceDeletedConsumer,
) -> KafkaConsumerClient:
    return KafkaConsumerClient(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        topic=settings.KAFKA_RESOURCE_PHYSICAL_DESTROY_TOPIC,
        group_id=settings.KAFKA_RAG_RESOURCE_DESTROY_GROUP_ID,
        handler=consumer.handle,
    )


class Container(containers.DeclarativeContainer):
    redis_client = providers.Singleton(_build_redis_client)
    qdrant_client = providers.Singleton(_build_qdrant_client)
    neo4j_driver = providers.Singleton(_build_neo4j_driver)
    qdrant_bm25_config = providers.Singleton(_build_qdrant_bm25_config)

    permission_filter_builder = providers.Singleton(RagPermissionFilterBuilder)
    vector_index_repository = providers.Singleton(
        QdrantRagVectorIndexRepository,
        client=qdrant_client,
        collection_name=settings.QDRANT_RAG_COLLECTION_NAME,
        dense_vector_size=settings.EMBEDDING_DIMENSIONS,
        embedding_profile=settings.EMBEDDING_MODEL,
        bm25_config=qdrant_bm25_config,
    )
    candidate_repository = providers.Singleton(
        QdrantRagCandidateRepository,
        client=qdrant_client,
        collection_name=settings.QDRANT_RAG_COLLECTION_NAME,
        permission_filter_builder=permission_filter_builder,
        bm25_config=qdrant_bm25_config,
    )
    acl_projector = providers.Singleton(RagAclProjector)
    acl_projection_repository = providers.Singleton(
        MongoRagAclProjectionRepository,
        projector=acl_projector,
        resource_database_name=settings.RESOURCE_PERMISSION_MONGODB_DB_NAME,
    )
    permission_authorizer = providers.Singleton(
        RagPermissionAuthorizer,
        repository=acl_projection_repository,
    )
    knowledge_graph_repository = providers.Singleton(
        Neo4jKnowledgeGraphRepository,
        driver=neo4j_driver,
        database=settings.NEO4J_DATABASE,
        permission_authorizer=permission_authorizer,
        permission_filter_builder=permission_filter_builder,
    )
    acl_recalculate_consumer = providers.Singleton(
        RagAclRecalculateConsumer,
        repository=acl_projection_repository,
        projection_targets=providers.List(
            vector_index_repository,
            knowledge_graph_repository,
        ),
    )
    acl_kafka_consumer = providers.Singleton(
        _build_rag_acl_kafka_consumer,
        consumer=acl_recalculate_consumer,
    )

    content_projection_repository = providers.Singleton(
        MongoRagContentProjectionRepository,
    )
    section_projector = providers.Singleton(RagSectionProjector)
    embedding_client = providers.Singleton(build_embedding_client)
    query_client = providers.Singleton(build_query_client)
    context_indexing_query_client = providers.Singleton(
        build_query_client,
        thinking="disabled",
    )
    context_indexing_cache = providers.Singleton(
        RedisRagContextIndexingCache,
        redis_client=redis_client,
        ttl_seconds=settings.RAG_CONTEXT_INDEXING_CACHE_TTL_SECONDS,
    )
    context_indexing = providers.Singleton(
        ContextIndexingService,
        client=context_indexing_query_client,
        cache=context_indexing_cache,
    )
    content_indexer = providers.Singleton(
        RagContentIndexer,
        projector=section_projector,
        projection_repository=content_projection_repository,
        vector_repository=vector_index_repository,
        acl_repository=acl_projection_repository,
        embedding_client=embedding_client,
        context_indexing=context_indexing,
    )

    graph_llm = providers.Singleton(QueryClientGraphRagLLM, client=query_client)
    graph_extraction_cache = providers.Singleton(
        RedisKnowledgeGraphExtractionCache,
        redis_client=redis_client,
        ttl_seconds=settings.RAG_GRAPH_EXTRACTION_CACHE_TTL_SECONDS,
    )
    graph_extractor = providers.Singleton(
        KnowledgeGraphExtractor,
        llm=graph_llm,
        cache=graph_extraction_cache,
        cache_profile=(
            f"{settings.LLM_BASE_URL}|{settings.QUERY_MODEL}|thinking=default"
        ),
    )
    knowledge_graph_indexer = providers.Singleton(
        KnowledgeGraphIndexer,
        content_repository=content_projection_repository,
        acl_repository=acl_projection_repository,
        extractor=graph_extractor,
        graph_repository=knowledge_graph_repository,
    )
    document_ready_consumer = providers.Singleton(
        RagDocumentReadyConsumer,
        content_indexer=content_indexer,
        graph_indexer=knowledge_graph_indexer,
    )
    document_kafka_consumer = providers.Singleton(
        _build_rag_document_kafka_consumer,
        consumer=document_ready_consumer,
    )
    resource_deleted_consumer = providers.Singleton(
        RagResourceDeletedConsumer,
        targets=providers.List(
            acl_projection_repository,
            content_projection_repository,
            vector_index_repository,
            knowledge_graph_repository,
        ),
    )
    resource_deleted_kafka_consumer = providers.Singleton(
        _build_rag_resource_deleted_kafka_consumer,
        consumer=resource_deleted_consumer,
    )

    candidate_retriever = providers.Singleton(
        RagCandidateRetriever,
        embedding_client=embedding_client,
        candidate_repository=candidate_repository,
        projection_repository=content_projection_repository,
        permission_authorizer=permission_authorizer,
        ranking_pipeline=providers.Object(KNOWLEDGE_SEARCH_PIPELINE),
    )
    evidence_materializer = providers.Singleton(
        RagEvidenceMaterializer,
        repository=content_projection_repository,
        permission_authorizer=permission_authorizer,
    )
    section_navigator = providers.Singleton(
        RagSectionNavigator,
        repository=content_projection_repository,
    )
    navigation_state_repository = providers.Singleton(
        RedisKnowledgeNavigationStateRepository,
        redis_client=redis_client,
        ttl_seconds=settings.RAG_NAVIGATION_STATE_TTL_SECONDS,
    )
    knowledge_navigation_service = providers.Singleton(
        KnowledgeNavigationService,
        retriever=candidate_retriever,
        permission_authorizer=permission_authorizer,
        graph_repository=knowledge_graph_repository,
        evidence_materializer=evidence_materializer,
        section_navigator=section_navigator,
        state_repository=navigation_state_repository,
        path_ranking_pipeline=providers.Object(KNOWLEDGE_GRAPH_PATH_PIPELINE),
    )


container = Container()
