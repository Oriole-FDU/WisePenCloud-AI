# src/chat/container.py

from typing import List

import httpx
from dependency_injector import containers, providers
from v2.nacos import NacosNamingService

from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.core.providers import (
    AnthropicAdapter,
    GeminiAdapter,
    LiteLLMAdapter,
    Mem0Adapter,
    OpenAIAdapter,
    OssFileLoader,
    QwenAdapter,
)
from chat.application.llm_provider_resolver import LLMProviderResolver
from chat.application.token_counter import TokenCounter
from chat.core.persistence import (
    MongoSessionRepository,
    MongoMessageRepository,
    MongoModelRepository,
    MongoProviderRepository,
    RedisHotContext,
)
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.agents import (
    DefaultAgentResolver,
)
from chat.application.tools.skill_tools.utils.skill_matcher import DefaultSkillMatcher
from chat.application.tools.skill_tools import CreateSkillInfoTool
from chat.application.tools.skill_tools import GetSkillInfoTool
from chat.application.tools.skill_tools import LoadSkillAssetTool
from chat.application.tools.skill_tools import LoadSkillTool
from chat.application.tools.skill_tools import UpdateSkillInfoTool
from chat.application.tools.skill_tools import UploadSkillDraftAssetTool
from chat.application.tools.core import ToolRegistry
from chat.application.tools.common.tool_content_store.store import (
    DEFAULT_TOOL_CONTENT_TTL_SECONDS,
    ToolContentStore,
)
from chat.application.tools.core.execution.dispatcher import ToolDispatcher
from chat.application.tools.search_tools.anysearch_search_tool import AnySearchSearchTool
from chat.application.tools.search_tools.baidu_qianfan_search_tool import BaiduQianfanSearchTool
from chat.application.tools.search_tools.exa_search_tool import ExaSearchTool
from chat.application.tools.search_tools.platform_search_tool import PlatformSearchTool
from chat.application.tools.search_tools.tavily_search_tool import TavilySearchTool
from chat.application.tools.search_tools.web_search.factories.integration_searcher_factory import (
    IntegrationSearcherFactory,
)
from chat.application.tools.search_tools.web_search.factories.platform_source_factory import (
    WebSearchPlatformSourceFactory,
)
from chat.application.tools.search_tools.web_search.runtime_context_resolver import (
    WebSearchRuntimeContextResolver,
)
from chat.application.tools.search_tools.web_search.searchers import (
    DdgSearcher,
    FourGetSearcher,
    PlatformDefaultSearcher,
    ProviderSearcher,
    SearchProviderConfig,
)
from chat.application.tools.search_tools.web_search.service import SearchService
from chat.application.tools.session_tools import (
    GetHistoricalChatMessagesTool,
    ToolContentRegexReadTool,
    ToolContentRerankReadTool,
    ToolContentSequentialReadTool,
)
from chat.application.tools.tool_output_cache import ToolOutputCache
from chat.application.tools.tool_output_renderer import ToolOutputRenderer
from chat.core.config.nacos import nacos_client_manager
from chat.core.persistence.mongo.web_search_credential_repository import (
    MongoWebSearchCredentialRepository,
)
from chat.core.persistence.redis.tool_content_repository import RedisToolContentRepository
from chat.core.security import SecretCipher
from chat.service_client import FileStorageClient, AIAssetClient, ResourceClient
from common.cloud.service_discovery import ServiceDiscovery
from common.http.rpc_client import RpcClient
from common.kafka.producer import KafkaProducerClient

WEB_SEARCH_HTTP_TIMEOUT_SECONDS = 15.0


async def _provide_nacos_naming() -> NacosNamingService:
    """延迟到首次 await，避免在 import 阶段触发 async Nacos 建连。"""
    return await nacos_client_manager.get_naming_client()


def _build_registry(tool_providers: List[providers.Provider]) -> ToolRegistry:
    """工厂函数：组装并返回已注册所有工具的 ToolRegistry 实例。"""
    registry = ToolRegistry()
    for provider in tool_providers:
        registry.register(provider)
    return registry


def _build_platform_default_searcher(
        *,
        http_client: httpx.AsyncClient,
) -> ProviderSearcher:
    return PlatformDefaultSearcher(
        fourget_searcher=FourGetSearcher(
            http_client=http_client,
            config=SearchProviderConfig(
                base_url=settings.WEB_SEARCH_FOURGET_BASE_URL,
                source_id="platform_default",
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

    session_repo = providers.Singleton(MongoSessionRepository)
    message_repo = providers.Singleton(MongoMessageRepository)
    model_repo = providers.Singleton(MongoModelRepository)
    provider_repo = providers.Singleton(MongoProviderRepository)
    secret_cipher = providers.Singleton(
        SecretCipher,
        encryption_key=settings.SECRET_ENCRYPTION_KEY,
    )
    web_search_credential_repo = providers.Singleton(
        MongoWebSearchCredentialRepository,
        secret_cipher=secret_cipher,
    )
    hot_context_repo = providers.Singleton(RedisHotContext)

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

    # 工具基础设施
    tool_content_repository = providers.Singleton(
        RedisToolContentRepository,
        redis_url=settings.REDIS_URL,
        ttl_seconds=DEFAULT_TOOL_CONTENT_TTL_SECONDS,
    )
    tool_content_store = providers.Singleton(
        ToolContentStore,
        repository=tool_content_repository,
    )
    tool_output_renderer = providers.Singleton(ToolOutputRenderer)
    tool_output_cache = providers.Singleton(
        ToolOutputCache,
        content_store=tool_content_store,
        inline_max_chars=settings.TOOL_RESULT_MAX_CHARS,
    )
    tool_dispatcher = providers.Singleton(
        ToolDispatcher,
        output_renderer=tool_output_renderer,
        output_cache=tool_output_cache,
    )

    # 搜索工具组件
    web_search_http_client = providers.Singleton(
        httpx.AsyncClient,
        timeout=httpx.Timeout(WEB_SEARCH_HTTP_TIMEOUT_SECONDS),
        trust_env=False,
    )
    platform_default_searcher = providers.Singleton(
        _build_platform_default_searcher,
        http_client=web_search_http_client,
    )
    web_search_runtime_context_resolver = providers.Singleton(
        WebSearchRuntimeContextResolver,
        credential_repository=web_search_credential_repo,
        platform_member_provider=settings.WEB_SEARCH_PLATFORM_MEMBER_PROVIDER,
        platform_member_api_key=settings.WEB_SEARCH_PLATFORM_MEMBER_API_KEY,
    )
    web_search_integration_searcher_factory = providers.Singleton(
        IntegrationSearcherFactory,
        http_client=web_search_http_client,
        exa_base_url=settings.WEB_SEARCH_EXA_BASE_URL,
        tavily_base_url=settings.WEB_SEARCH_TAVILY_BASE_URL,
        anysearch_base_url=settings.WEB_SEARCH_ANYSEARCH_BASE_URL,
        baidu_qianfan_base_url=settings.WEB_SEARCH_BAIDU_QIANFAN_BASE_URL,
    )
    web_search_platform_source_factory = providers.Singleton(
        WebSearchPlatformSourceFactory,
        platform_default_searcher=platform_default_searcher,
        integration_searcher_factory=web_search_integration_searcher_factory,
    )
    web_search_service = providers.Singleton(SearchService)

    # 工具层：各 Tool 和 ToolRegistry 均为 Singleton，由容器统一管理生命周期
    search_history_tool = providers.Singleton(
        GetHistoricalChatMessagesTool,
        message_repo=message_repo,
        max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
    )
    tool_content_rerank_read_tool = providers.Singleton(
        ToolContentRerankReadTool,
        content_store=tool_content_store,
    )
    tool_content_regex_read_tool = providers.Singleton(
        ToolContentRegexReadTool,
        content_store=tool_content_store,
    )
    tool_content_sequential_read_tool = providers.Singleton(
        ToolContentSequentialReadTool,
        content_store=tool_content_store,
    )
    platform_search_tool = providers.Singleton(
        PlatformSearchTool,
        service=web_search_service,
        platform_source_factory=web_search_platform_source_factory,
        runtime_context_resolver=web_search_runtime_context_resolver,
    )
    exa_search_tool = providers.Singleton(
        ExaSearchTool,
        service=web_search_service,
        integration_searcher_factory=web_search_integration_searcher_factory,
        credential_repository=web_search_credential_repo,
    )
    tavily_search_tool = providers.Singleton(
        TavilySearchTool,
        service=web_search_service,
        integration_searcher_factory=web_search_integration_searcher_factory,
        credential_repository=web_search_credential_repo,
    )
    anysearch_search_tool = providers.Singleton(
        AnySearchSearchTool,
        service=web_search_service,
        integration_searcher_factory=web_search_integration_searcher_factory,
        credential_repository=web_search_credential_repo,
    )
    baidu_qianfan_search_tool = providers.Singleton(
        BaiduQianfanSearchTool,
        service=web_search_service,
        integration_searcher_factory=web_search_integration_searcher_factory,
        credential_repository=web_search_credential_repo,
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
    create_skill_info_tool = providers.Singleton(
        CreateSkillInfoTool,
        ai_asset_client=ai_asset_client,
    )
    get_skill_info_tool = providers.Singleton(
        GetSkillInfoTool,
        ai_asset_client=ai_asset_client,
    )
    update_skill_info_tool = providers.Singleton(
        UpdateSkillInfoTool,
        ai_asset_client=ai_asset_client,
    )
    upload_skill_draft_asset_tool = providers.Singleton(
        UploadSkillDraftAssetTool,
        ai_asset_client=ai_asset_client,
    )

    tool_providers = providers.List(
        search_history_tool,
        tool_content_rerank_read_tool,
        tool_content_regex_read_tool,
        tool_content_sequential_read_tool,
        platform_search_tool,
        exa_search_tool,
        tavily_search_tool,
        anysearch_search_tool,
        baidu_qianfan_search_tool,
        load_skill_tool,
        load_skill_asset_tool,
        create_skill_info_tool,
        get_skill_info_tool,
        update_skill_info_tool,
        upload_skill_draft_asset_tool,
    )

    tool_registry = providers.Singleton(
        _build_registry,
        tool_providers=tool_providers,
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
        web_search_credential_repo=web_search_credential_repo,
        kafka_producer=kafka_producer,
        skill_matcher=skill_matcher,
        agent_resolver=agent_resolver,
    )


# 全局容器实例
container = Container()
