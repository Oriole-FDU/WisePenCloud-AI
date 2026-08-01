# src/chat/container.py

from collections.abc import AsyncIterator
from typing import List

import redis.asyncio as redis
from dependency_injector import containers, providers
from scrapling.fetchers import AsyncStealthySession, FetcherSession
from v2.nacos import NacosNamingService
from zeroentropy import AsyncZeroEntropy

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
from chat.application.token_counter import TokenCounter
from chat.core.persistence import (
    MongoSessionRepository,
    MongoMessageRepository,
    MongoModelRepository,
    MongoMcpServerConfigRepository,
    MongoProviderRepository,
    MongoToolConfigRepository,
    RedisHotContext,
    RedisMcpToolDiscoveryCache,
    RedisToolContentRepository,
    RedisWebContentCacheRepository,
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
from common.utils.ranking import RankingPipeline
from common.utils.ranking.fusion import WeightedRrfFusion
from common.utils.ranking.rerankers import (
    ZeroEntropyReranker,
    ZeroEntropyRerankerConfig,
)
from common.utils.ranking.scorers import (
    BM25Scorer,
    FieldedBM25Scorer,
    FieldedBM25ScorerConfig,
)
from common.utils.ranking.tokenizer import ThuLacRankingTokenizer
from chat.application.tools.core.mcp import (
    McpClient,
    McpToolCatalog,
    SystemMcpToolCatalog,
)
from chat.application.tools.session_tools.get_historical_chat_messages_tool import (
    GetHistoricalChatMessagesTool,
)
from chat.application.tools.web_tools import (
    DocumentLinkExtractTool,
    WebCrawlTool,
    WebFetchTool,
)
from chat.application.tools.web_tools.document_link_extract import (
    DocumentLinkExtractor,
)
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


def _build_read_ranked_expand_pipeline() -> RankingPipeline:
    tokenizer = ThuLacRankingTokenizer()
    return RankingPipeline(
        scorers=(
            BM25Scorer(tokenizer=tokenizer),
            FieldedBM25Scorer(
                tokenizer=tokenizer,
                config=FieldedBM25ScorerConfig(
                    field_weights={"section": 2.0, "anchor": 1.5},
                ),
            ),
        ),
        fusion=WeightedRrfFusion(),
        reranker=ZeroEntropyReranker(
            client=AsyncZeroEntropy(api_key=settings.ZERO_ENTROPY_API_KEY),
            config=ZeroEntropyRerankerConfig(model=settings.RERANKER_MODEL),
        ),
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
        timeout=settings.MCP_DEFAULT_TIMEOUT_SECONDS,
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
    read_ranked_expand_pipeline = providers.Singleton(
        _build_read_ranked_expand_pipeline,
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
    document_link_extractor = providers.Singleton(
        DocumentLinkExtractor,
        session=web_fetch_static_session,
        content_cache_repository=web_content_cache_repository,
    )
    document_link_extract_tool = providers.Singleton(
        DocumentLinkExtractTool,
        extractor=document_link_extractor,
    )
    web_crawl_tool = providers.Singleton(
        WebCrawlTool,
        crawler=web_crawler,
    )
    tool_providers = providers.List(
        search_history_tool,
        load_skill_tool,
        load_skill_asset_tool,
        tool_content_read_tool,
        tool_content_regex_read_tool,
        tool_content_ranked_expand_read_tool,
        web_fetch_tool,
        document_link_extract_tool,
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
