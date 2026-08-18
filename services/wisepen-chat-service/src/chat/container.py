# src/chat/container.py

from collections.abc import AsyncIterator

from common.cloud.service_discovery import ServiceDiscovery
from common.http.rpc_client import RpcClient
from common.kafka.producer import KafkaProducerClient
from dependency_injector import containers, providers
from scrapling.fetchers import AsyncStealthySession, FetcherSession
from v2.nacos import NacosNamingService

from chat.application.agents import (
    CompositeAgentResolver,
    DefaultAgentResolver,
    RemoteAgentResolver,
)
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.chat_turn_stream_manager import ChatTurnStreamManager
from chat.application.chat_turn_tool_policy import ChatTurnToolPolicyBuilder
from chat.application.llm_provider_resolver import LLMProviderResolver
from chat.application.token_counter import TokenCounter
from chat.application.tools.core import ToolRegistry
from chat.application.tools.core.execution.dispatcher import ToolDispatcher
from chat.application.tools.core.mcp import (
    McpClient,
    McpToolCatalog,
    SystemMcpToolCatalog,
)
from chat.application.tools.core.output_cache.cache_manager import ToolOutputCache
from chat.application.tools.core.output_cache.cache_store import ToolContentStore
from chat.application.tools.session_tools.cached_tool_output_tools import (
    CachedToolOutputInspectStructureTool,
    CachedToolOutputReadByPageTool,
    CachedToolOutputReadByRangeTool,
    CachedToolOutputReadBySectionTool,
    CachedToolOutputSearchByRegexTool,
    CachedToolOutputSearchBySemanticsTool,
)
from chat.application.tools.session_tools.get_historical_chat_messages_tool import (
    GetHistoricalChatMessagesTool,
)
from chat.application.tools.session_tools.load_image_attachment_tool import (
    LoadImageAttachmentTool,
)
from chat.application.tools.skill_tools import LoadSkillAssetTool, LoadSkillTool
from chat.application.tools.skill_tools.utils.skill_matcher import DefaultSkillMatcher
from chat.application.tools.web_tools import WebCrawlTool, WebFetchTool
from chat.application.tools.web_tools.common import WebContentCache
from chat.application.tools.web_tools.fetchers import (
    StaticPageFetcher,
    StealthyPageFetcher,
)
from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.core.config.nacos import nacos_client_manager
from chat.core.persistence import (
    MongoMcpServerConfigRepository,
    MongoMessageRepository,
    MongoModelRepository,
    MongoProviderRepository,
    MongoSessionRepository,
    MongoSuspendedChatRepository,
    MongoToolConfigRepository,
    RedisChatTurnStream,
    RedisHotContext,
    RedisMcpToolDiscoveryCache,
    RedisToolContentRepository,
    RedisWebContentCacheRepository,
)
from chat.core.providers import (
    AnthropicAdapter,
    GeminiAdapter,
    IflytekSpeechProvider,
    LiteLLMAdapter,
    Mem0Adapter,
    OpenAIAdapter,
    OssFileLoader,
    QwenAdapter,
)
from chat.domain.repositories import ToolConfigRepository
from chat.service_client import (
    AIAssetClient,
    FileStorageClient,
    McpServiceClient,
    ResourceClient,
)


async def _provide_nacos_naming() -> NacosNamingService:
    """延迟到首次 await，避免在 import 阶段触发 async Nacos 建连。"""
    return await nacos_client_manager.get_naming_client()


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
    # 浏览器只在 static 失败或正文质量不足时启用，避免所有 URL 承担重型渲染成本。
    session = AsyncStealthySession(
        headless=True,
        max_pages=3,
        timeout=30_000,
        disable_resources=False,
        block_ads=True,
        network_idle=False,
        load_dom=True,
        wait=500,
        solve_cloudflare=False,
        retries=2,
    )
    await session.start()
    try:
        yield session
    finally:
        await session.close()


def _build_registry(
        tool_providers: list[providers.Provider],
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
    suspended_chat_repo = providers.Singleton(MongoSuspendedChatRepository)
    mcp_server_config_repo = providers.Singleton(MongoMcpServerConfigRepository)
    hot_context_repo = providers.Singleton(RedisHotContext)
    mcp_tool_discovery_cache_repo = providers.Singleton(RedisMcpToolDiscoveryCache)
    chat_turn_stream_repo = providers.Singleton(RedisChatTurnStream)
    tool_content_repo = providers.Singleton(
        RedisToolContentRepository,
        ttl_seconds=settings.TOOL_CONTENT_DEFAULT_TTL_SECONDS,
    )
    tool_content_store = providers.Singleton(
        ToolContentStore,
        tool_content_repository=tool_content_repo,
        max_chars=settings.TOOL_CONTENT_MAX_CHARS,
    )
    web_content_cache_repository = providers.Singleton(RedisWebContentCacheRepository)
    web_content_cache = providers.Singleton(
        WebContentCache,
        repository=web_content_cache_repository,
    )
    tool_output_cache = providers.Singleton(
        ToolOutputCache,
        tool_content_store=tool_content_store,
    )
    tool_dispatcher = providers.Singleton(
        ToolDispatcher,
        output_cache=tool_output_cache,
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
    default_agent_resolver = providers.Singleton(DefaultAgentResolver)
    remote_agent_resolver = providers.Singleton(
        RemoteAgentResolver,
        ai_asset_client=ai_asset_client,
    )
    agent_resolver = providers.Singleton(
        CompositeAgentResolver,
        primary=remote_agent_resolver,
        fallback=default_agent_resolver,
    )

    # Skill 子系统：
    # - SkillRepository 从 Java ai-asset 读取 Skill
    # DefaultSkillMatcher
    skill_matcher = providers.Singleton(
        DefaultSkillMatcher,
        ai_asset_client=ai_asset_client,
    )
    chat_turn_tool_policy_builder = providers.Singleton(
        ChatTurnToolPolicyBuilder,
        skill_matcher=skill_matcher,
    )
    kafka_producer = providers.Singleton(
        KafkaProducerClient,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
    )

    # 工具层：各 Tool 和 ToolRegistry 均为 Singleton，由容器统一管理生命周期
    # GetHistoricalChatMessagesTool
    search_history_tool = providers.Singleton(
        GetHistoricalChatMessagesTool,
        message_repo=message_repo,
    )
    load_image_attachment_tool = providers.Singleton(
        LoadImageAttachmentTool,
        file_loader=oss_file_loader,
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
    inspect_cached_tool_output_structure_tool = providers.Singleton(
        CachedToolOutputInspectStructureTool,
        store=tool_content_store,
    )
    read_cached_tool_output_by_page_tool = providers.Singleton(
        CachedToolOutputReadByPageTool,
        store=tool_content_store,
    )
    read_cached_tool_output_by_range_tool = providers.Singleton(
        CachedToolOutputReadByRangeTool,
        store=tool_content_store,
    )
    read_cached_tool_output_by_section_tool = providers.Singleton(
        CachedToolOutputReadBySectionTool,
        store=tool_content_store,
    )
    search_cached_tool_output_by_regex_tool = providers.Singleton(
        CachedToolOutputSearchByRegexTool,
        store=tool_content_store,
    )
    search_cached_tool_output_by_semantics_tool = providers.Singleton(
        CachedToolOutputSearchBySemanticsTool,
        store=tool_content_store,
    )
    web_fetch_static_session = providers.Resource(_provide_web_fetch_static_session)
    web_fetch_browser_session = providers.Resource(_provide_web_fetch_browser_session)
    web_static_page_fetcher = providers.Singleton(
        StaticPageFetcher,
        session=web_fetch_static_session,
    )
    web_browser_page_fetcher = providers.Singleton(
        StealthyPageFetcher,
        session=web_fetch_browser_session,
    )
    web_fetch_tool = providers.Singleton(
        WebFetchTool,
        static_fetcher=web_static_page_fetcher,
        stealthy_fetcher=web_browser_page_fetcher,
        cache=web_content_cache,
    )
    web_crawl_tool = providers.Singleton(
        WebCrawlTool,
        static_fetcher=web_static_page_fetcher,
        stealthy_fetcher=web_browser_page_fetcher,
        cache=web_content_cache,
    )
    tool_providers = providers.List(
        search_history_tool,
        load_image_attachment_tool,
        load_skill_tool,
        load_skill_asset_tool,
        inspect_cached_tool_output_structure_tool,
        read_cached_tool_output_by_page_tool,
        read_cached_tool_output_by_range_tool,
        read_cached_tool_output_by_section_tool,
        search_cached_tool_output_by_regex_tool,
        search_cached_tool_output_by_semantics_tool,
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
        tool_policy_builder=chat_turn_tool_policy_builder,
        suspended_chat_repo=suspended_chat_repo,
        agent_resolver=agent_resolver,
        oss_file_loader=oss_file_loader,
    )

    chat_turn_stream_manager = providers.Singleton(
        ChatTurnStreamManager,
        stream_repo=chat_turn_stream_repo,
    )


# 全局容器实例
container = Container()
