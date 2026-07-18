# src/chat/container.py

from typing import List

from dependency_injector import containers, providers
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
from chat.application.tools.core.mcp import McpClient, McpToolCatalog, SystemMcpToolCatalog
from chat.application.tools.session_tools.get_historical_chat_messages_tool import GetHistoricalChatMessagesTool
from chat.core.config.nacos import nacos_client_manager
from chat.service_client import FileStorageClient, AIAssetClient, McpServiceClient, ResourceClient
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
    hot_context_repo = providers.Singleton(RedisHotContext)
    mcp_tool_discovery_cache_repo = providers.Singleton(RedisMcpToolDiscoveryCache)

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
    tool_providers = providers.List(
        search_history_tool,
        load_skill_tool,
        load_skill_asset_tool,
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
        kafka_producer=kafka_producer,
        skill_matcher=skill_matcher,
        agent_resolver=agent_resolver,
    )


# 全局容器实例
container = Container()
