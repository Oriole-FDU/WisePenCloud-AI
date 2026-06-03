"""应用级依赖注入容器。管理所有组件的生命周期与装配关系。"""

from typing import Any

from dependency_injector import containers, providers
from pymongo import AsyncMongoClient
from qdrant_client import models
from v2.nacos import NacosNamingService
from zeroentropy import AsyncZeroEntropy

from chat.application.api_service.rag import RagApiService
from chat.application.api_service.search_provider import SearchProviderConfigApiService
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.model_resolver import ModelResolver
from chat.application.rag.domain.answerability import EvidenceSufficiencyEvaluator
from chat.application.rag.domain.candidate_fusion import RagCandidateFusion
from chat.application.rag.domain.parent_aggregation import RagParentAggregator
from chat.application.rag.implementations.gc.scheduler import RagIndexGcScheduler
from chat.application.rag.implementations.gc.service import RagIndexGcService
from chat.application.rag.implementations.indexing.chunker import ChunkingConfig, RagChunker
from chat.application.rag.implementations.indexing.context_builder import (
    RagContextBuilder,
    RagContextBuilderConfig,
)
from chat.application.rag.implementations.indexing.index_builder import RagResourceIndexBuilder
from chat.application.rag.implementations.indexing.indexing_text_builder import RagIndexingTextBuilder
from chat.application.rag.implementations.indexing.processor import RagIndexProcessor
from chat.application.rag.implementations.indexing.runner import RagIndexWorkerRunner
from chat.application.rag.implementations.indexing.worker import RagIndexWorker
from chat.application.rag.implementations.persistence.elasticsearch.keyword_indexer import (
    ElasticsearchClientConfig,
    ElasticsearchKeywordIndexer,
    build_elasticsearch_client,
)
from chat.application.rag.implementations.persistence.mongodb.repositories.cache_repository import (
    MongoRagContextCacheRepository,
    MongoRagDenseEmbeddingCacheRepository,
    MongoRagQueryEmbeddingCacheRepository,
)
from chat.application.rag.implementations.persistence.mongodb.repositories.chunk_repository import (
    MongoChunkRepository,
)
from chat.application.rag.implementations.persistence.mongodb.repositories.manifest_repository import (
    MongoManifestRepository,
)
from chat.application.rag.implementations.persistence.mongodb.repositories.resource_repository import (
    MongoDocumentResourceRepository,
    MongoNoteResourceRepository,
)
from chat.application.rag.implementations.persistence.qdrant.collection import (
    QdrantCollectionConfig,
    QdrantCollectionManager,
    build_qdrant_client,
)
from chat.application.rag.implementations.persistence.qdrant.indexer import QdrantChunkIndexer
from chat.application.rag.implementations.persistence.redis.indexing_queue import RedisRagIndexingQueue
from chat.application.rag.implementations.providers.context_client import (
    LiteLLMContextClient,
    LiteLLMContextClientConfig,
)
from chat.application.rag.implementations.providers.dense import (
    CachedDenseEmbeddingClient,
    LiteLLMDenseEmbeddingClient,
    LiteLLMDenseEmbeddingClientConfig,
)
from chat.application.rag.implementations.resources.resource_handlers import (
    DocumentResourceHandler,
    NoteResourceHandler,
)
from chat.application.rag.implementations.resources.resource_service import ResourceService
from chat.application.rag.implementations.resources.version_service import (
    RagPipelineVersionConfig,
    RagVersionService,
)
from chat.application.rag.implementations.retrieval.context_assembler import RagContextAssembler
from chat.application.rag.implementations.retrieval.elasticsearch_retriever import (
    ElasticsearchKeywordRetriever,
)
from chat.application.rag.implementations.retrieval.evidence_assembler import RagEvidenceAssembler
from chat.application.rag.implementations.retrieval.manifest_resolver import RagManifestResolver
from chat.application.rag.implementations.retrieval.qdrant_retriever import QdrantChunkRetriever
from chat.application.rag.implementations.retrieval.reranker import ZeroEntropyReranker
from chat.application.rag.implementations.retrieval.retrieval_orchetrator import (
    RagRetrievalOrchestrator,
)
from chat.application.rag.implementations.retrieval.retrieval_pipeline import RagRetrievalPipeline
from chat.application.rag.service import RagService
from chat.application.skill_cache_refresher import SkillCacheRefresher
from chat.application.skill_matcher import KeywordSkillMatcher
from chat.application.tools.web.services.web_search.provider_policy.encryption import (
    SearchProviderCredentialCipher,
)
from chat.application.tools.web.services.web_search.provider_policy.persistence.repositories import (
    SearchProviderConfigRepository,
)
from chat.application.tools.web.services.web_search.provider_policy.service import (
    SearchProviderConfigService,
)
from chat.application.tools.web.services.web_search.provider_policy.validator import (
    SearchProviderConfigValidator,
)
from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.core.config.tool_settings import tool_settings
from chat.core.persistence import (
    MongoMessageRepository,
    MongoSessionRepository,
    MongoSkillRepository,
    RedisHotContext,
)
from chat.core.providers import LiteLLMAdapter, LocalFSSkillAssetLoader, Mem0Adapter, OssSkillAssetLoader
from chat.tool_container import register_tools
from common.clients.file_storage import FileStorageClient
from common.cloud.nacos_client import nacos_client_manager
from common.cloud.service_discovery import ServiceDiscovery
from common.http.rpc_client import RpcClient
from common.kafka.producer import KafkaProducerClient


class Container(containers.DeclarativeContainer):
    """应用级依赖注入容器。"""
    pass


# ==============================================================================
#   Helper Resources / Factories
# ==============================================================================


async def _provide_nacos_naming() -> NacosNamingService:
    """延迟到首次 await，避免在 import 阶段触发 async Nacos 建连。"""
    return await nacos_client_manager.get_naming_client()


# ==============================================================================
#   1. 核心服务层 (Core Services)
# ==============================================================================

def _register_core(container_cls: Any) -> None:

    # ----- LLM & Memory -----
    container_cls.llm_provider = providers.Singleton(LiteLLMAdapter)

    container_cls.memory_provider = providers.Singleton(Mem0Adapter)

    container_cls.model_resolver = providers.Singleton(ModelResolver)

    # ----- Kafka -----
    container_cls.kafka_producer = providers.Singleton(
        KafkaProducerClient,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
    )


# ==============================================================================
#   2. 数据持久化层 (Persistence - MongoDB / Redis)
# ==============================================================================

def _register_persistence(container_cls: Any) -> None:

    # ----- MongoDB -----
    container_cls.mongo_client = providers.Singleton(
        AsyncMongoClient,
        settings.MONGODB_URL,
    )

    container_cls.session_repo = providers.Singleton(MongoSessionRepository)

    container_cls.message_repo = providers.Singleton(MongoMessageRepository)

    container_cls.skill_repo = providers.Singleton(MongoSkillRepository)

    # ----- Redis -----
    container_cls.hot_context_repo = providers.Singleton(RedisHotContext)


# ==============================================================================
#   3. 内部 RPC 与服务发现 (RPC / Service Discovery)
# ==============================================================================

def _register_rpc(container_cls: Any) -> None:

    # ----- Service Discovery -----
    container_cls.service_discovery = providers.Singleton(
        ServiceDiscovery,
        naming_client_provider=providers.Object(_provide_nacos_naming),
        group_name=bootstrap_settings.NACOS_GROUP,
        default_strategy=settings.RPC_LB_STRATEGY,
        cache_ttl_seconds=settings.SERVICE_DISCOVERY_CACHE_TTL_SECONDS,
    )

    # ----- RPC Client -----
    container_cls.rpc_client = providers.Singleton(
        RpcClient,
        discovery=container_cls.service_discovery,
        from_source_secret=settings.FROM_SOURCE_SECRET,
        timeout=settings.RPC_DEFAULT_TIMEOUT,
        retries=settings.RPC_DEFAULT_RETRIES,
        default_strategy=settings.RPC_LB_STRATEGY,
    )

    # ----- File Storage -----
    container_cls.file_storage_client = providers.Singleton(
        FileStorageClient,
        rpc=container_cls.rpc_client,
    )


# ==============================================================================
#   4. Skill 插件系统 (Skill System)
# ==============================================================================

def _register_skill(container_cls: Any) -> None:

    # ----- Asset Loaders -----
    container_cls.oss_skill_asset_loader = providers.Singleton(
        OssSkillAssetLoader,
        file_storage_client=container_cls.file_storage_client,
    )

    if settings.DEV:
        container_cls.skill_asset_loader = providers.Singleton(
            LocalFSSkillAssetLoader,
            oss_fallback=container_cls.oss_skill_asset_loader,
        )
    else:
        container_cls.skill_asset_loader = container_cls.oss_skill_asset_loader

    # ----- Matcher & Cache -----
    container_cls.skill_matcher = providers.Singleton(
        KeywordSkillMatcher,
        skill_repo=container_cls.skill_repo,
    )

    container_cls.skill_cache_refresher = providers.Singleton(
        SkillCacheRefresher,
        matcher=container_cls.skill_matcher,
    )


# ==============================================================================
#   5. RAG 检索增强生成 (RAG Pipeline)
# ==============================================================================

def _register_rag(container_cls: Any) -> None:

    # ----- 1. 版本控制 -----
    container_cls.rag_pipeline_version_config = providers.Singleton(
        RagPipelineVersionConfig,
        chunker_version=settings.RAG_CHUNKER_VERSION,
        semantic_indexing_text_version=settings.RAG_SEMANTIC_INDEXING_TEXT_VERSION,
        keyword_indexing_version=settings.RAG_KEYWORD_INDEXING_VERSION,
        identifier_extractor_version=settings.RAG_IDENTIFIER_EXTRACTOR_VERSION,
        dense_embedding_model_version=settings.RAG_DENSE_EMBEDDING_MODEL_VERSION,
        sparse_embedding_model_version=settings.RAG_SPARSE_EMBEDDING_MODEL_VERSION,
        contextual_indexing_version=settings.RAG_CONTEXTUAL_INDEXING_VERSION,
        context_model_version=settings.RAG_CONTEXT_MODEL_VERSION,
        context_prompt_version=settings.RAG_CONTEXT_PROMPT_VERSION,
    )

    container_cls.rag_version_service = providers.Singleton(
        RagVersionService,
        pipeline_config=container_cls.rag_pipeline_version_config,
    )

    # ----- 2. MongoDB 仓储 -----
    container_cls.rag_note_resource_repository = providers.Singleton(
        MongoNoteResourceRepository,
    )

    container_cls.rag_document_resource_repository = providers.Singleton(
        MongoDocumentResourceRepository,
    )

    container_cls.rag_manifest_repository = providers.Singleton(MongoManifestRepository)

    container_cls.rag_chunk_repository = providers.Singleton(
        MongoChunkRepository,
        mongo_client=container_cls.mongo_client,
    )

    container_cls.rag_context_cache_repository = providers.Singleton(
        MongoRagContextCacheRepository,
    )

    container_cls.rag_dense_embedding_cache_repository = providers.Singleton(
        MongoRagDenseEmbeddingCacheRepository,
    )

    container_cls.rag_query_embedding_cache_repository = providers.Singleton(
        MongoRagQueryEmbeddingCacheRepository,
    )

    # ----- 3. Redis 索引队列 -----
    container_cls.rag_indexing_queue = providers.Singleton(RedisRagIndexingQueue)

    # ----- 4. Qdrant 向量检索 -----
    container_cls.rag_qdrant_client = providers.Singleton(build_qdrant_client)

    container_cls.rag_dense_embedding_client_config = providers.Singleton(
        LiteLLMDenseEmbeddingClientConfig,
        model=settings.RAG_DENSE_EMBEDDING_MODEL,
        api_base=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        dimensions=settings.RAG_DENSE_VECTOR_SIZE,
    )

    container_cls.rag_qdrant_collection_config = providers.Singleton(
        QdrantCollectionConfig,
        collection_name=settings.RAG_QDRANT_COLLECTION_NAME,
        dense_vector_size=settings.RAG_DENSE_VECTOR_SIZE,
        dense_distance=models.Distance.COSINE,
    )

    container_cls.rag_qdrant_collection_manager = providers.Singleton(
        QdrantCollectionManager,
        client=container_cls.rag_qdrant_client,
        config=container_cls.rag_qdrant_collection_config,
    )

    container_cls.rag_qdrant_chunk_indexer = providers.Singleton(
        QdrantChunkIndexer,
        client=container_cls.rag_qdrant_client,
        config=container_cls.rag_qdrant_collection_config,
    )

    container_cls.rag_dense_embedding_client = providers.Singleton(
        LiteLLMDenseEmbeddingClient,
        config=container_cls.rag_dense_embedding_client_config,
    )

    container_cls.rag_qdrant_chunk_retriever = providers.Singleton(
        QdrantChunkRetriever,
        client=container_cls.rag_qdrant_client,
        config=container_cls.rag_qdrant_collection_config,
        dense_embedding_client=container_cls.rag_dense_embedding_client,
        query_embedding_cache_repository=container_cls.rag_query_embedding_cache_repository,
        query_embedding_model_version=settings.RAG_DENSE_EMBEDDING_MODEL_VERSION,
    )

    # ----- 5. Elasticsearch 关键词索引 -----
    container_cls.rag_elasticsearch_client_config = providers.Singleton(
        ElasticsearchClientConfig,
        uris=settings.ELASTICSEARCH_URIS,
        username=settings.ELASTICSEARCH_USERNAME,
        password=settings.ELASTICSEARCH_PASSWORD,
    )

    container_cls.rag_elasticsearch_client = providers.Singleton(
        build_elasticsearch_client,
        config=container_cls.rag_elasticsearch_client_config,
    )

    container_cls.rag_elasticsearch_keyword_indexer = providers.Singleton(
        ElasticsearchKeywordIndexer,
        client=container_cls.rag_elasticsearch_client,
        index_name=settings.RAG_ELASTICSEARCH_INDEX_NAME,
    )

    container_cls.rag_elasticsearch_keyword_retriever = providers.Singleton(
        ElasticsearchKeywordRetriever,
        client=container_cls.rag_elasticsearch_client,
        index_name=settings.RAG_ELASTICSEARCH_INDEX_NAME,
    )

    # ----- 6. 切块 (Chunking) -----
    container_cls.rag_chunking_config = providers.Singleton(ChunkingConfig)

    container_cls.rag_chunker = providers.Singleton(
        RagChunker,
        config=container_cls.rag_chunking_config,
    )

    # ----- 7. LLM 上下文增强 (Contextual Indexing) -----
    container_cls.rag_context_client_config = providers.Singleton(
        LiteLLMContextClientConfig,
        model=settings.RAG_CONTEXT_MODEL,
        api_base=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        max_tokens=settings.RAG_CONTEXT_MAX_TOKENS,
        temperature=settings.RAG_CONTEXT_TEMPERATURE,
    )

    container_cls.rag_context_client = providers.Singleton(
        LiteLLMContextClient,
        config=container_cls.rag_context_client_config,
    )

    container_cls.rag_context_builder_config = providers.Singleton(RagContextBuilderConfig)

    container_cls.rag_context_builder = providers.Singleton(
        RagContextBuilder,
        context_client=container_cls.rag_context_client,
        config=container_cls.rag_context_builder_config,
        cache_repository=container_cls.rag_context_cache_repository,
    )

    container_cls.rag_indexing_text_builder = providers.Singleton(RagIndexingTextBuilder)

    # ----- 8. 密集向量嵌入 (Dense Embedding) -----


    container_cls.rag_cached_dense_embedding_client = providers.Singleton(
        CachedDenseEmbeddingClient,
        inner_client=container_cls.rag_dense_embedding_client,
        model_version=settings.RAG_DENSE_EMBEDDING_MODEL_VERSION,
    )

    # ----- 9. 索引构建流水线 (Ingestion Pipeline) -----
    container_cls.rag_note_resource_handler = providers.Singleton(
        NoteResourceHandler,
        repository=container_cls.rag_note_resource_repository,
    )

    container_cls.rag_document_resource_handler = providers.Singleton(
        DocumentResourceHandler,
        repository=container_cls.rag_document_resource_repository,
    )

    container_cls.rag_resource_service = providers.Singleton(
        ResourceService,
        handlers=providers.List(
            container_cls.rag_note_resource_handler,
            container_cls.rag_document_resource_handler,
        ),
        manifest_repository=container_cls.rag_manifest_repository,
        version_service=container_cls.rag_version_service,
        index_message_repository=container_cls.rag_indexing_queue,
    )

    container_cls.rag_resource_index_builder = providers.Singleton(
        RagResourceIndexBuilder,
        chunker=container_cls.rag_chunker,
        context_builder=container_cls.rag_context_builder,
        indexing_text_builder=container_cls.rag_indexing_text_builder,
        chunk_repository=container_cls.rag_chunk_repository,
        dense_embedding_client=container_cls.rag_dense_embedding_client,
        qdrant_collection_manager=container_cls.rag_qdrant_collection_manager,
        qdrant_chunk_indexer=container_cls.rag_qdrant_chunk_indexer,
        elasticsearch_keyword_indexer=container_cls.rag_elasticsearch_keyword_indexer,
        manifest_repository=container_cls.rag_manifest_repository,
    )

    container_cls.rag_index_processor = providers.Singleton(
        RagIndexProcessor,
        resource_service=container_cls.rag_resource_service,
        version_service=container_cls.rag_version_service,
        index_builder=container_cls.rag_resource_index_builder,
        manifest_repository=container_cls.rag_manifest_repository,
    )

    container_cls.rag_index_worker = providers.Singleton(
        RagIndexWorker,
        indexing_queue_repository=container_cls.rag_indexing_queue,
        processor=container_cls.rag_index_processor,
        consumer_group=settings.RAG_INDEX_CONSUMER_GROUP,
        consumer_name=settings.SERVICE_NAME,
    )

    container_cls.rag_index_worker_runner = providers.Singleton(
        RagIndexWorkerRunner,
        indexing_queue=container_cls.rag_indexing_queue,
        worker=container_cls.rag_index_worker,
        consumer_group=settings.RAG_INDEX_CONSUMER_GROUP,
    )

    # ----- 10. 多路检索 & 重排 -----
    container_cls.rag_manifest_resolver = providers.Singleton(
        RagManifestResolver,
        manifest_repository=container_cls.rag_manifest_repository,
    )

    container_cls.rag_retrieval_service = providers.Singleton(
        RagRetrievalOrchestrator,
        manifest_resolver=container_cls.rag_manifest_resolver,
        qdrant_retriever=container_cls.rag_qdrant_chunk_retriever,
        elasticsearch_retriever=container_cls.rag_elasticsearch_keyword_retriever,
    )

    container_cls.rag_candidate_fusion = providers.Singleton(RagCandidateFusion)

    container_cls.rag_evidence_assembler = providers.Singleton(
        RagEvidenceAssembler,
        chunk_repository=container_cls.rag_chunk_repository,
    )

    container_cls.rag_parent_aggregator = providers.Singleton(RagParentAggregator)

    # ----- 11. ZeroEntropy 重排 -----
    container_cls.rag_zero_entropy_client = providers.Singleton(
        AsyncZeroEntropy,
        api_key=settings.ZERO_ENTROPY_API_KEY,
        base_url=settings.ZERO_ENTROPY_BASE_URL,
        timeout=settings.ZERO_ENTROPY_TIMEOUT_SECONDS,
    )

    container_cls.rag_reranker = providers.Singleton(
        ZeroEntropyReranker,
        client=container_cls.rag_zero_entropy_client,
        model=settings.RAG_RERANKER_ZE_MODEL,
    )

    # ----- 12. 完备性评估 -----
    container_cls.rag_evidence_sufficiency_evaluator = providers.Singleton(
        EvidenceSufficiencyEvaluator,
    )

    # ----- 13. 检索管线总控 -----
    container_cls.rag_retrieval_pipeline = providers.Singleton(
        RagRetrievalPipeline,
        retrieval_orchestrator=container_cls.rag_retrieval_service,
        candidate_fusion=container_cls.rag_candidate_fusion,
        evidence_assembler=container_cls.rag_evidence_assembler,
        reranker=container_cls.rag_reranker,
        sufficiency_evaluator=container_cls.rag_evidence_sufficiency_evaluator,
        parent_aggregator=container_cls.rag_parent_aggregator,
    )

    container_cls.rag_context_assembler = providers.Singleton(RagContextAssembler)

    container_cls.rag_service = providers.Singleton(
        RagService,
        resource_service=container_cls.rag_resource_service,
        version_service=container_cls.rag_version_service,
        manifest_repository=container_cls.rag_manifest_repository,
        retrieval_pipeline=container_cls.rag_retrieval_pipeline,
        context_assembler=container_cls.rag_context_assembler,
    )

    # ----- 14. GC 清理常驻 Worker -----
    container_cls.rag_index_gc_service = providers.Singleton(
        RagIndexGcService,
        manifest_repository=container_cls.rag_manifest_repository,
        chunk_repository=container_cls.rag_chunk_repository,
        qdrant_chunk_indexer=container_cls.rag_qdrant_chunk_indexer,
        elasticsearch_keyword_indexer=container_cls.rag_elasticsearch_keyword_indexer,
    )

    container_cls.rag_index_gc_scheduler = providers.Singleton(
        RagIndexGcScheduler,
        gc_service=container_cls.rag_index_gc_service,
    )


# ==============================================================================
#   11. 应用层服务 (Application Services)
# ==============================================================================

def _register_application(container_cls: Any) -> None:

    # ----- Search Provider Config -----
    container_cls.search_provider_config_repository = providers.Singleton(
        SearchProviderConfigRepository,
    )

    container_cls.search_provider_credential_cipher = providers.Singleton(
        SearchProviderCredentialCipher,
        master_key=tool_settings.SEARCH_PROVIDER_CREDENTIAL_MASTER_KEY,
        key_id=tool_settings.SEARCH_PROVIDER_CREDENTIAL_KEY_ID,
    )

    container_cls.search_provider_config_validator = providers.Singleton(
        SearchProviderConfigValidator,
        client=container_cls.web_search_http_client,
        cache=container_cls.web_search_cache,
    )

    container_cls.search_provider_config_service = providers.Singleton(
        SearchProviderConfigService,
        repository=container_cls.search_provider_config_repository,
        cipher=container_cls.search_provider_credential_cipher,
        validator=container_cls.search_provider_config_validator,
    )

    container_cls.search_provider_config_api_service = providers.Singleton(
        SearchProviderConfigApiService,
        service=container_cls.search_provider_config_service,
    )

    container_cls.rag_api_service = providers.Singleton(
        RagApiService,
        rag_service=container_cls.rag_service,
    )

    # ----- Chat Coordinator -----
    container_cls.chat_turn_coordinator = providers.Factory(
        ChatTurnCoordinator,
        llm=container_cls.llm_provider,
        memory=container_cls.memory_provider,
        model_resolver=container_cls.model_resolver,
        session_repo=container_cls.session_repo,
        message_repo=container_cls.message_repo,
        hot_context_repo=container_cls.hot_context_repo,
        tool_registry=container_cls.tool_registry,
        kafka_producer=container_cls.kafka_producer,
        skill_matcher=container_cls.skill_matcher,
        tool_output_aspect=container_cls.tool_output_aspect,
        search_provider_config_service=container_cls.search_provider_config_service,
    )


# ==============================================================================
#   Build Entry
# ==============================================================================

def build_container() -> Container:
    _register_core(Container)
    _register_persistence(Container)
    _register_rpc(Container)
    _register_skill(Container)
    _register_rag(Container)
    register_tools(Container)
    _register_application(Container)

    return Container()


container = build_container()

__all__ = [
    "Container",
    "container",
]
