# src/chat/container.py

from collections.abc import AsyncIterator
from typing import List

import httpx
import redis.asyncio as redis
from dependency_injector import containers, providers
from neo4j import AsyncDriver, AsyncGraphDatabase
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models
from scrapling.fetchers import AsyncStealthySession, FetcherSession
from v2.nacos import NacosNamingService

from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.core.providers import (
    LiteLLMAdapter,
    AnthropicAdapter,
    GeminiAdapter,
    OpenAIAdapter,
    QwenAdapter,
    Mem0Adapter,
    OssFileLoader,
    IflytekSpeechProvider,
)
from chat.application.llm_provider_resolver import LLMProviderResolver
from chat.application.rag.acl import RagAclProjector, RagPermissionAuthorizer
from chat.application.rag.ingestion import (
    ContextIndexingService,
    RagContentIndexer,
    RagSectionProjector,
)
from chat.application.rag.evidence import RagEvidenceMaterializer
from chat.application.rag.graph_extraction import (
    KnowledgeGraphExtractor,
    QueryClientGraphRagLLM,
)
from chat.application.rag.graph_projection import KnowledgeGraphIndexer
from chat.application.rag.knowledge_navigation import KnowledgeNavigationService
from chat.application.rag.section_navigation import RagSectionNavigator
from chat.application.rag.kafka_consumers import (
    RagAclRecalculateConsumer,
    RagDocumentReadyConsumer,
    RagResourceDeletedConsumer,
)
from chat.application.rag.retrieval import (
    RagCandidateRetriever,
    RagPermissionFilterBuilder,
)
from chat.application.token_counter import TokenCounter
from chat.core.persistence import (
    MongoSessionRepository,
    MongoMessageRepository,
    MongoModelRepository,
    MongoMcpServerConfigRepository,
    MongoRagAclProjectionRepository,
    MongoRagContentProjectionRepository,
    MongoProviderRepository,
    MongoToolConfigRepository,
    Neo4jKnowledgeGraphRepository,
    RedisHotContext,
    RedisKnowledgeGraphExtractionCache,
    RedisRagContextIndexingCache,
    RedisKnowledgeNavigationStateRepository,
    RedisMcpToolDiscoveryCache,
    RedisToolContentRepository,
    RedisWebContentCacheRepository,
    QdrantRagVectorIndexRepository,
    QdrantRagCandidateRepository,
)
from chat.domain.repositories import ToolConfigRepository
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.agents import (
    DefaultAgentResolver,
)
from chat.application.tools.skill_tools.utils.skill_matcher import DefaultSkillMatcher
from chat.application.tools.skill_tools import LoadSkillAssetTool
from chat.application.tools.skill_tools import LoadSkillTool
from chat.application.tools.core import ToolRegistry
from chat.application.tools.core.execution.dispatcher import ToolDispatcher
from chat.application.tools.core.output.cache import ToolOutputCache
from chat.application.tools.common.tool_content_store import ToolContentStore
from chat.application.tools.session_tools.tool_content_read.tools import (
    ToolContentReadTool,
    ToolContentRegexReadTool,
    ToolContentRankedExpandReadTool,
)
from chat.application.tools.session_tools.tool_content_read.services.reader import (
    ToolContentReader,
)
from chat.application.tools.rag_tools import (
    KnowledgeNavigateExpandTool,
    KnowledgeNavigateLocateTool,
    KnowledgeNavigateSectionsTool,
)
from chat.application.utils.ranking.presets import (
    KNOWLEDGE_GRAPH_PATH_PIPELINE,
    KNOWLEDGE_SEARCH_PIPELINE,
    READ_RANKED_EXPAND_PIPELINE,
    WEB_SEARCH_PIPELINE,
)
from chat.application.utils.llm_clients import (
    build_embedding_client,
    build_query_client,
)
from chat.application.tools.core.mcp import (
    McpClient,
    McpToolCatalog,
    SystemMcpToolCatalog,
)
from chat.application.tools.session_tools.get_historical_chat_messages_tool import (
    GetHistoricalChatMessagesTool,
)
from chat.application.tools.search_tools.web_search import (
    AnySearchSearchTool,
    BaiduQianfanSearchTool,
    ExaSearchTool,
    FirecrawlSearchTool,
    PlatformSearchTool,
    TavilySearchTool,
    TinyFishSearchTool,
)
from chat.application.tools.search_tools.web_search.services.pipeline import (
    SearchPipeline,
)
from chat.application.tools.search_tools.web_search.services.providers import (
    DdgSearcher,
    FourGetSearcher,
    PlatformDefaultSearcher,
)
from chat.application.tools.search_tools.web_search.services.providers.base import (
    SearchProviderConfig,
)
from chat.application.tools.search_tools.web_search.services.sources import (
    SearchSourceFactory,
)
from chat.application.tools.web_tools import WebCrawlTool, WebFetchTool
from chat.application.tools.web_tools.web_fetch import (
    FetchCoordinator,
    StaticPageFetcher,
    StealthyPageFetcher,
    WebCrawler,
)
from chat.core.config.nacos import nacos_client_manager
from chat.service_client import (
    FileStorageClient,
    AIAssetClient,
    McpServiceClient,
    ResourceClient,
)
from common.cloud.service_discovery import ServiceDiscovery
from common.http.rpc_client import RpcClient
from common.kafka import KafkaConsumerClient
from common.kafka.producer import KafkaProducerClient


async def _provide_nacos_naming() -> NacosNamingService:
    """延迟到首次 await，避免在 import 阶段触发 async Nacos 建连。"""
    return await nacos_client_manager.get_naming_client()


def _build_registry(
    tool_providers: List[providers.Provider],
    tool_config_repo: ToolConfigRepository,
    mcp_tool_catalog: McpToolCatalog,
    system_mcp_tool_catalog: SystemMcpToolCatalog,
) -> ToolRegistry:
    """工厂函数：组装并返回已注册所有工具的 ToolRegistry 实例。"""
    registry = ToolRegistry(
        tool_config_repo=tool_config_repo,
        mcp_tool_catalog=mcp_tool_catalog,
        system_mcp_tool_catalog=system_mcp_tool_catalog,
    )
    for provider in tool_providers:
        registry.register(provider)
    return registry


def _get_iflytek_speech_config():
    if settings.SPEECH_CONFIG is None:
        return None
    return settings.SPEECH_CONFIG.IFLYTEK


def _build_redis_client() -> redis.Redis:
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


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


def _build_qdrant_client() -> AsyncQdrantClient:
    host = settings.QDRANT_HOST.strip()
    if not host:
        raise ValueError("QDRANT_HOST must not be empty")
    return AsyncQdrantClient(
        host=host,
        port=settings.QDRANT_PORT,
        api_key=settings.QDRANT_PASSWORD or None,
        https=False,
        # Document 必须原样发送给 Qdrant Server，由其内置 BM25 生成 sparse vector。
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


async def _provide_web_fetch_static_session() -> AsyncIterator[FetcherSession]:
    async with FetcherSession(
        impersonate="chrome",
        stealthy_headers=True,
        follow_redirects=False,
        timeout=30.0,
        retries=1,
    ) as session:
        yield session


async def _provide_web_fetch_browser_session() -> AsyncIterator[AsyncStealthySession]:
    session = AsyncStealthySession(
        headless=True,
        max_pages=3,
        timeout=30_000,
        disable_resources=True,
        block_ads=True,
        network_idle=False,
        load_dom=True,
        retries=1,
    )
    await session.start()
    try:
        yield session
    finally:
        await session.close()


def _build_platform_default_searcher(
    http_client: httpx.AsyncClient,
) -> PlatformDefaultSearcher:
    return PlatformDefaultSearcher(
        fourget_searcher=FourGetSearcher(
            http_client=http_client,
            config=SearchProviderConfig(
                base_url=settings.WEB_SEARCH_FOURGET_BASE_URL,
            ),
        ),
        ddg_searcher=DdgSearcher(),
    )


class Container(containers.DeclarativeContainer):
    """依赖注入容器，管理单例对象的生命周期。"""

    qwen_adapter = providers.Singleton(QwenAdapter)
    openai_adapter = providers.Singleton(OpenAIAdapter)
    anthropic_adapter = providers.Singleton(AnthropicAdapter)
    gemini_adapter = providers.Singleton(GeminiAdapter)
    litellm_adapter = providers.Singleton(LiteLLMAdapter)
    llm_provider_resolver = providers.Singleton(
        LLMProviderResolver,
        qwen_adapter=qwen_adapter,
        openai_adapter=openai_adapter,
        anthropic_adapter=anthropic_adapter,
        gemini_adapter=gemini_adapter,
        litellm_adapter=litellm_adapter,
    )
    token_counter = providers.Singleton(TokenCounter)
    memory_provider = providers.Singleton(Mem0Adapter)
    iflytek_speech_provider = providers.Singleton(
        IflytekSpeechProvider,
        config=providers.Callable(_get_iflytek_speech_config),
    )

    session_repo = providers.Singleton(MongoSessionRepository)
    message_repo = providers.Singleton(MongoMessageRepository)
    model_repo = providers.Singleton(MongoModelRepository)
    provider_repo = providers.Singleton(MongoProviderRepository)
    tool_config_repo = providers.Singleton(MongoToolConfigRepository)
    mcp_server_config_repo = providers.Singleton(MongoMcpServerConfigRepository)
    redis_client = providers.Singleton(_build_redis_client)
    hot_context_repo = providers.Singleton(
        RedisHotContext,
        redis_client=redis_client,
    )
    mcp_tool_discovery_cache_repo = providers.Singleton(
        RedisMcpToolDiscoveryCache,
        redis_client=redis_client,
    )

    # 内部 RPC：Nacos 服务发现 + 通用 httpx 客户端 + file-storage typed facade
    service_discovery = providers.Singleton(
        ServiceDiscovery,
        naming_client_provider=providers.Object(_provide_nacos_naming),
        group_name=bootstrap_settings.NACOS_GROUP,
        default_strategy=settings.RPC_LB_STRATEGY,
        cache_ttl_seconds=settings.SERVICE_DISCOVERY_CACHE_TTL_SECONDS,
    )
    rpc_client = providers.Singleton(
        RpcClient,
        discovery=service_discovery,
        from_source_secret=settings.FROM_SOURCE_SECRET,
        timeout=settings.RPC_DEFAULT_TIMEOUT,
        retries=settings.RPC_DEFAULT_RETRIES,
        default_strategy=settings.RPC_LB_STRATEGY,
    )
    file_storage_client = providers.Singleton(
        FileStorageClient,
        rpc=rpc_client,
    )
    ai_asset_client = providers.Singleton(
        AIAssetClient,
        rpc=rpc_client,
    )
    resource_client = providers.Singleton(
        ResourceClient,
        rpc=rpc_client,
    )
    mcp_service_client = providers.Singleton(
        McpServiceClient,
        discovery=service_discovery,
        from_source_secret=settings.FROM_SOURCE_SECRET,
        timeout=settings.RPC_DEFAULT_TIMEOUT,
        default_strategy=settings.RPC_LB_STRATEGY,
    )
    mcp_client = providers.Singleton(
        McpClient,
        timeout=settings.MCP_DEFAULT_TIMEOUT_SECONDS,
    )
    mcp_tool_catalog = providers.Singleton(
        McpToolCatalog,
        mcp_client=mcp_client,
        mcp_tool_discovery_cache_repo=mcp_tool_discovery_cache_repo,
        mcp_server_config_repo=mcp_server_config_repo,
    )
    system_mcp_tool_catalog = providers.Singleton(
        SystemMcpToolCatalog,
        mcp_service_client=mcp_service_client,
    )

    # OssFileLoader
    oss_file_loader = providers.Singleton(
        OssFileLoader,
        file_storage_client=file_storage_client,
        cache_dir=settings.OSS_CACHE_DIR,
        cache_ttl_seconds=settings.OSS_CACHE_TTL_SECONDS,
        gc_interval_seconds=settings.OSS_CACHE_GC_INTERVAL_SECONDS,
    )

    # Skill 子系统：
    # - SkillRepository 从 Java ai-asset 读取 Skill
    # DefaultSkillMatcher
    skill_matcher = providers.Singleton(
        DefaultSkillMatcher,
        ai_asset_client=ai_asset_client,
    )
    agent_resolver = providers.Singleton(DefaultAgentResolver)
    kafka_producer = providers.Singleton(
        KafkaProducerClient,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
    )
    qdrant_client = providers.Singleton(_build_qdrant_client)
    neo4j_driver = providers.Singleton(_build_neo4j_driver)
    rag_qdrant_bm25_config = providers.Singleton(_build_qdrant_bm25_config)
    rag_permission_filter_builder = providers.Singleton(RagPermissionFilterBuilder)
    rag_vector_index_repository = providers.Singleton(
        QdrantRagVectorIndexRepository,
        client=qdrant_client,
        collection_name=settings.QDRANT_RAG_COLLECTION_NAME,
        dense_vector_size=settings.EMBEDDING_DIMENSIONS,
        embedding_profile=settings.EMBEDDING_MODEL,
        bm25_config=rag_qdrant_bm25_config,
    )
    rag_candidate_repository = providers.Singleton(
        QdrantRagCandidateRepository,
        client=qdrant_client,
        collection_name=settings.QDRANT_RAG_COLLECTION_NAME,
        permission_filter_builder=rag_permission_filter_builder,
        bm25_config=rag_qdrant_bm25_config,
    )
    rag_acl_projector = providers.Singleton(RagAclProjector)
    rag_acl_projection_repository = providers.Singleton(
        MongoRagAclProjectionRepository,
        projector=rag_acl_projector,
        resource_database_name=settings.RESOURCE_PERMISSION_MONGODB_DB_NAME,
    )
    rag_permission_authorizer = providers.Singleton(
        RagPermissionAuthorizer,
        repository=rag_acl_projection_repository,
    )
    rag_knowledge_graph_repository = providers.Singleton(
        Neo4jKnowledgeGraphRepository,
        driver=neo4j_driver,
        database=settings.NEO4J_DATABASE,
        permission_authorizer=rag_permission_authorizer,
        permission_filter_builder=rag_permission_filter_builder,
    )
    rag_acl_recalculate_consumer = providers.Singleton(
        RagAclRecalculateConsumer,
        repository=rag_acl_projection_repository,
        projection_targets=providers.List(
            rag_vector_index_repository,
            rag_knowledge_graph_repository,
        ),
    )
    rag_acl_kafka_consumer = providers.Singleton(
        _build_rag_acl_kafka_consumer,
        consumer=rag_acl_recalculate_consumer,
    )
    rag_content_projection_repository = providers.Singleton(
        MongoRagContentProjectionRepository,
    )
    rag_section_projector = providers.Singleton(RagSectionProjector)
    rag_embedding_client = providers.Singleton(build_embedding_client)
    rag_query_client = providers.Singleton(build_query_client)
    rag_context_indexing_query_client = providers.Singleton(
        build_query_client,
        thinking="disabled",
    )
    rag_context_indexing_cache = providers.Singleton(
        RedisRagContextIndexingCache,
        redis_client=redis_client,
        ttl_seconds=settings.RAG_CONTEXT_INDEXING_CACHE_TTL_SECONDS,
    )
    rag_context_indexing = providers.Singleton(
        ContextIndexingService,
        client=rag_context_indexing_query_client,
        cache=rag_context_indexing_cache,
    )
    rag_content_indexer = providers.Singleton(
        RagContentIndexer,
        projector=rag_section_projector,
        projection_repository=rag_content_projection_repository,
        vector_repository=rag_vector_index_repository,
        acl_repository=rag_acl_projection_repository,
        embedding_client=rag_embedding_client,
        context_indexing=rag_context_indexing,
    )
    knowledge_search_pipeline = providers.Object(KNOWLEDGE_SEARCH_PIPELINE)
    knowledge_graph_path_pipeline = providers.Object(KNOWLEDGE_GRAPH_PATH_PIPELINE)
    read_ranked_expand_pipeline = providers.Object(READ_RANKED_EXPAND_PIPELINE)
    web_search_ranking_pipeline = providers.Object(WEB_SEARCH_PIPELINE)
    rag_graph_llm = providers.Singleton(
        QueryClientGraphRagLLM,
        client=rag_query_client,
    )
    rag_graph_extraction_cache = providers.Singleton(
        RedisKnowledgeGraphExtractionCache,
        redis_client=redis_client,
        ttl_seconds=settings.RAG_GRAPH_EXTRACTION_CACHE_TTL_SECONDS,
    )
    rag_graph_extractor = providers.Singleton(
        KnowledgeGraphExtractor,
        llm=rag_graph_llm,
        cache=rag_graph_extraction_cache,
        cache_profile=(
            f"{settings.LLM_BASE_URL}|{settings.QUERY_MODEL}|thinking=default"
        ),
    )
    rag_knowledge_graph_indexer = providers.Singleton(
        KnowledgeGraphIndexer,
        content_repository=rag_content_projection_repository,
        acl_repository=rag_acl_projection_repository,
        extractor=rag_graph_extractor,
        graph_repository=rag_knowledge_graph_repository,
    )
    rag_candidate_retriever = providers.Singleton(
        RagCandidateRetriever,
        embedding_client=rag_embedding_client,
        candidate_repository=rag_candidate_repository,
        projection_repository=rag_content_projection_repository,
        permission_authorizer=rag_permission_authorizer,
        ranking_pipeline=knowledge_search_pipeline,
    )
    rag_evidence_materializer = providers.Singleton(
        RagEvidenceMaterializer,
        repository=rag_content_projection_repository,
        permission_authorizer=rag_permission_authorizer,
    )
    rag_section_navigator = providers.Singleton(
        RagSectionNavigator,
        repository=rag_content_projection_repository,
    )
    knowledge_navigation_state_repository = providers.Singleton(
        RedisKnowledgeNavigationStateRepository,
        redis_client=redis_client,
        ttl_seconds=settings.TOOL_CONTENT_DEFAULT_TTL_SECONDS,
    )
    knowledge_navigation_service = providers.Singleton(
        KnowledgeNavigationService,
        retriever=rag_candidate_retriever,
        permission_authorizer=rag_permission_authorizer,
        graph_repository=rag_knowledge_graph_repository,
        evidence_materializer=rag_evidence_materializer,
        section_navigator=rag_section_navigator,
        state_repository=knowledge_navigation_state_repository,
        path_ranking_pipeline=knowledge_graph_path_pipeline,
    )
    rag_document_ready_consumer = providers.Singleton(
        RagDocumentReadyConsumer,
        content_indexer=rag_content_indexer,
        graph_indexer=rag_knowledge_graph_indexer,
    )
    rag_document_kafka_consumer = providers.Singleton(
        _build_rag_document_kafka_consumer,
        consumer=rag_document_ready_consumer,
    )
    rag_resource_deleted_consumer = providers.Singleton(
        RagResourceDeletedConsumer,
        # ACL 先失效，后续任一存储清理失败时查询仍保持 fail closed。
        targets=providers.List(
            rag_acl_projection_repository,
            rag_content_projection_repository,
            rag_vector_index_repository,
            rag_knowledge_graph_repository,
        ),
    )
    rag_resource_deleted_kafka_consumer = providers.Singleton(
        _build_rag_resource_deleted_kafka_consumer,
        consumer=rag_resource_deleted_consumer,
    )

    tool_content_repository = providers.Singleton(
        RedisToolContentRepository,
        redis_client=redis_client,
        ttl_seconds=settings.TOOL_CONTENT_DEFAULT_TTL_SECONDS,
    )
    tool_content_store = providers.Singleton(
        ToolContentStore,
        repository=tool_content_repository,
        max_chars=settings.TOOL_CONTENT_MAX_CHARS,
    )
    knowledge_navigate_locate_tool = providers.Singleton(
        KnowledgeNavigateLocateTool,
        service=knowledge_navigation_service,
    )
    knowledge_navigate_expand_tool = providers.Singleton(
        KnowledgeNavigateExpandTool,
        service=knowledge_navigation_service,
    )
    knowledge_navigate_sections_tool = providers.Singleton(
        KnowledgeNavigateSectionsTool,
        service=knowledge_navigation_service,
    )
    # 工具层：各 Tool 和 ToolRegistry 均为 Singleton，由容器统一管理生命周期
    # GetHistoricalChatMessagesTool
    search_history_tool = providers.Singleton(
        GetHistoricalChatMessagesTool,
        message_repo=message_repo,
    )
    # LoadSkillTool / LoadSkillAssetTool
    load_skill_tool = providers.Singleton(
        LoadSkillTool,
        ai_asset_client=ai_asset_client,
        resource_client=resource_client,
        file_loader=oss_file_loader,
    )
    load_skill_asset_tool = providers.Singleton(
        LoadSkillAssetTool,
        ai_asset_client=ai_asset_client,
        resource_client=resource_client,
        file_loader=oss_file_loader,
    )
    tool_content_reader = providers.Singleton(
        ToolContentReader,
        max_window_chars=settings.TOOL_RESULT_MAX_CHARS,
        ranking_pipeline=read_ranked_expand_pipeline,
        store=tool_content_store,
    )
    tool_content_read_tool = providers.Singleton(
        ToolContentReadTool,
        reader=tool_content_reader,
    )
    tool_content_regex_read_tool = providers.Singleton(
        ToolContentRegexReadTool,
        reader=tool_content_reader,
    )
    tool_content_ranked_expand_read_tool = providers.Singleton(
        ToolContentRankedExpandReadTool,
        reader=tool_content_reader,
    )
    web_search_http_client = providers.Singleton(
        httpx.AsyncClient,
        timeout=httpx.Timeout(15.0),
    )
    platform_default_searcher = providers.Singleton(
        _build_platform_default_searcher,
        http_client=web_search_http_client,
    )
    web_search_source_factory = providers.Singleton(
        SearchSourceFactory,
        http_client=web_search_http_client,
        platform_default_searcher=platform_default_searcher,
        exa_base_url=settings.WEB_SEARCH_EXA_BASE_URL,
        tavily_base_url=settings.WEB_SEARCH_TAVILY_BASE_URL,
        anysearch_base_url=settings.WEB_SEARCH_ANYSEARCH_BASE_URL,
        baidu_qianfan_base_url=settings.WEB_SEARCH_BAIDU_QIANFAN_BASE_URL,
        tinyfish_base_url=settings.WEB_SEARCH_TINYFISH_BASE_URL,
        firecrawl_base_url=settings.WEB_SEARCH_FIRECRAWL_BASE_URL,
    )
    web_search_pipeline = providers.Singleton(
        SearchPipeline,
        ranking_pipeline=web_search_ranking_pipeline,
    )
    platform_search_tool = providers.Singleton(
        PlatformSearchTool,
        search_pipeline=web_search_pipeline,
        source_factory=web_search_source_factory,
    )
    exa_search_tool = providers.Singleton(
        ExaSearchTool,
        search_pipeline=web_search_pipeline,
        source_factory=web_search_source_factory,
    )
    tavily_search_tool = providers.Singleton(
        TavilySearchTool,
        search_pipeline=web_search_pipeline,
        source_factory=web_search_source_factory,
    )
    anysearch_search_tool = providers.Singleton(
        AnySearchSearchTool,
        search_pipeline=web_search_pipeline,
        source_factory=web_search_source_factory,
    )
    baidu_qianfan_search_tool = providers.Singleton(
        BaiduQianfanSearchTool,
        search_pipeline=web_search_pipeline,
        source_factory=web_search_source_factory,
    )
    tinyfish_search_tool = providers.Singleton(
        TinyFishSearchTool,
        search_pipeline=web_search_pipeline,
        source_factory=web_search_source_factory,
    )
    firecrawl_search_tool = providers.Singleton(
        FirecrawlSearchTool,
        search_pipeline=web_search_pipeline,
        source_factory=web_search_source_factory,
    )
    web_content_cache_repository = providers.Singleton(
        RedisWebContentCacheRepository,
        redis_client=redis_client,
    )
    web_fetch_static_session = providers.Resource(
        _provide_web_fetch_static_session,
    )
    web_fetch_browser_session = providers.Resource(
        _provide_web_fetch_browser_session,
    )
    web_static_page_fetcher = providers.Singleton(
        StaticPageFetcher,
        session=web_fetch_static_session,
    )
    web_browser_page_fetcher = providers.Singleton(
        StealthyPageFetcher,
        session=web_fetch_browser_session,
    )
    web_fetch_coordinator = providers.Singleton(
        FetchCoordinator,
        static_fetcher=web_static_page_fetcher,
        stealthy_fetcher=web_browser_page_fetcher,
        content_cache_repository=web_content_cache_repository,
    )
    web_crawler = providers.Singleton(
        WebCrawler,
        static_fetcher=web_static_page_fetcher,
        stealthy_fetcher=web_browser_page_fetcher,
        content_cache_repository=web_content_cache_repository,
    )
    web_fetch_tool = providers.Singleton(
        WebFetchTool,
        fetch_coordinator=web_fetch_coordinator,
    )
    web_crawl_tool = providers.Singleton(
        WebCrawlTool,
        crawler=web_crawler,
    )
    tool_providers = providers.List(
        knowledge_navigate_locate_tool,
        knowledge_navigate_expand_tool,
        knowledge_navigate_sections_tool,
        search_history_tool,
        load_skill_tool,
        load_skill_asset_tool,
        tool_content_read_tool,
        tool_content_regex_read_tool,
        tool_content_ranked_expand_read_tool,
        platform_search_tool,
        exa_search_tool,
        tavily_search_tool,
        anysearch_search_tool,
        baidu_qianfan_search_tool,
        tinyfish_search_tool,
        firecrawl_search_tool,
        web_fetch_tool,
        web_crawl_tool,
    )

    tool_registry = providers.Singleton(
        _build_registry,
        tool_providers=tool_providers,
        tool_config_repo=tool_config_repo,
        mcp_tool_catalog=mcp_tool_catalog,
        system_mcp_tool_catalog=system_mcp_tool_catalog,
    )

    tool_output_cache = providers.Singleton(
        ToolOutputCache,
        content_store=tool_content_store,
        inline_max_chars=settings.TOOL_RESULT_MAX_CHARS,
    )
    tool_dispatcher = providers.Singleton(
        ToolDispatcher,
        output_cache=tool_output_cache,
    )

    # Application 层组件
    chat_turn_coordinator = providers.Factory(
        ChatTurnCoordinator,
        llm_provider_resolver=llm_provider_resolver,
        text_llm=litellm_adapter,
        token_counter=token_counter,
        memory=memory_provider,
        model_repo=model_repo,
        provider_repo=provider_repo,
        session_repo=session_repo,
        message_repo=message_repo,
        hot_context_repo=hot_context_repo,
        tool_registry=tool_registry,
        tool_dispatcher=tool_dispatcher,
        kafka_producer=kafka_producer,
        skill_matcher=skill_matcher,
        agent_resolver=agent_resolver,
    )


# 全局容器实例
container = Container()
