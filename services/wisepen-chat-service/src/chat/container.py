# src/chat/container.py

from typing import List

import redis.asyncio as redis
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
    DefaultLlmCompactor,
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
    MongoSuspendedChatRepository,
    RedisHotContext,
    RedisMcpToolDiscoveryCache,
    RedisChatTurnStream,
    RedisChatTurnQueue,
    RedisFrontendToolResponseRepository,
    RedisToolContentRepository,
)
from chat.domain.repositories import ToolConfigRepository
from chat.application.chat_context_assembler import ChatContextAssembler
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.chat_turn_tool_policy import ChatTurnToolPolicyBuilder
from chat.application.chat_turn_stream_manager import ChatTurnStreamManager
from chat.application.frontend_tool_response_broker import FrontendToolResponseBroker
from chat.mq import FrontendToolResponseConsumer
from chat.application.agents import (
    DefaultAgentResolver,
    CompositeAgentResolver,
    RemoteAgentResolver,
)
from chat.application.tools.skill_tools.utils.skill_matcher import DefaultSkillMatcher
from chat.application.tools.skill_tools import LoadSkillAssetTool
from chat.application.tools.skill_tools import LoadSkillTool
from chat.application.tools.core import ToolRegistry
from chat.application.tools.core.execution.dispatcher import ToolDispatcher
from chat.application.tools.core.mcp import McpClient, McpToolCatalog, SystemMcpToolCatalog
from chat.application.tools.core.output_cache import (
    ToolContentStore,
    ToolOutputCache,
)
from chat.application.tools.session_tools.get_historical_chat_messages_tool import GetHistoricalChatMessagesTool
from chat.application.tools.session_tools.load_image_attachment_tool import LoadImageAttachmentTool
from chat.application.tools.session_tools.tool_output import (
    InspectToolOutputStructureTool,
    ReadToolOutputRangeTool,
    ReadToolOutputSectionTool,
    SearchToolOutputBM25Tool,
    SearchToolOutputRegexTool,
)
from chat.application.tools.byok_tools.bind_model_provider import BindModelProviderTool
from chat.application.tools.byok_tools.create_my_model import CreateMyModelTool
from chat.application.tools.byok_tools.create_my_provider import CreateMyProviderTool
from chat.application.tools.byok_tools.delete_my_model import DeleteMyModelTool
from chat.application.tools.byok_tools.delete_my_provider import DeleteMyProviderTool
from chat.application.tools.byok_tools.list_available_models import ListAvailableModelsTool
from chat.application.tools.byok_tools.list_my_providers import ListMyProvidersTool
from chat.application.tools.byok_tools.unbind_model_provider import UnbindModelProviderTool
from chat.application.tools.byok_tools.update_my_model import UpdateMyModelTool
from chat.application.tools.byok_tools.update_my_provider import UpdateMyProviderTool
from chat.application.tools.session_tools.compact_session import CompactSessionTool
from chat.application.tools.session_tools.get_session_messages import GetSessionMessagesTool
from chat.application.tools.session_tools.list_sessions import ListSessionsTool
from chat.application.tools.session_tools.rename_session import RenameSessionTool
from chat.application.tools.memory_tools.add_memory import AddMemoryTool
from chat.application.tools.memory_tools.delete_memory import DeleteMemoryTool
from chat.application.tools.memory_tools.list_memories import ListMemoriesTool
from chat.application.tools.memory_tools.search_memories import SearchMemoriesTool
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
    if settings.integrations.speech is None:
        return None
    return settings.integrations.speech.iflytek


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

    redis_client = providers.Singleton(redis.from_url, settings.persistence.redis.url, decode_responses=True,)
    hot_context_repo = providers.Singleton(RedisHotContext, redis_client=redis_client)
    mcp_tool_discovery_cache_repo = providers.Singleton(RedisMcpToolDiscoveryCache, redis_client=redis_client)
    chat_turn_stream_repo = providers.Singleton(RedisChatTurnStream, redis_client=redis_client)
    chat_turn_queue_repo = providers.Singleton(RedisChatTurnQueue, redis_client=redis_client)
    frontend_tool_response_repo = providers.Singleton(
        RedisFrontendToolResponseRepository,
        redis_client=redis_client,
    )
    tool_content_repo = providers.Singleton(RedisToolContentRepository, redis_client=redis_client)

    tool_content_store = providers.Singleton(ToolContentStore,tool_content_repository=tool_content_repo,)
    output_cache = providers.Singleton(ToolOutputCache, tool_content_store=tool_content_store)

    tool_dispatcher = providers.Singleton(ToolDispatcher, output_cache=output_cache)

    # 内部 RPC：Nacos 服务发现 + 通用 httpx 客户端 + file-storage typed facade
    service_discovery = providers.Singleton(
        ServiceDiscovery,
        naming_client_provider=providers.Object(_provide_nacos_naming),
        group_name=bootstrap_settings.NACOS_GROUP,
        default_strategy=settings.rpc.lb_strategy,
        cache_ttl_seconds=settings.service_discovery.cache_ttl_seconds,
    )
    rpc_client = providers.Singleton(
        RpcClient,
        discovery=service_discovery,
        from_source_secret=settings.security.from_source_secret,
        timeout=settings.rpc.default_timeout_seconds,
        retries=settings.rpc.default_retries,
        default_strategy=settings.rpc.lb_strategy,
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
        from_source_secret=settings.security.from_source_secret,
        service_name="wisepen-mcp-service",
        timeout=settings.rpc.default_timeout_seconds,
        default_strategy=settings.rpc.lb_strategy,
    )
    sandbox_mcp_service_client = providers.Singleton(
        McpServiceClient,
        discovery=service_discovery,
        from_source_secret=settings.security.from_source_secret,
        service_name="wisepen-sandbox-service",
        mcp_path="/mcp/",
        timeout=settings.mcp.default_timeout_seconds,
        default_strategy=settings.rpc.lb_strategy,
    )
    mcp_client = providers.Singleton(
        McpClient,
        timeout=settings.mcp.default_timeout_seconds,
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
        sandbox_mcp_service_client=sandbox_mcp_service_client,
    )

    # OssFileLoader
    oss_file_loader = providers.Singleton(
        OssFileLoader,
        file_storage_client=file_storage_client,
        cache_dir=settings.asset_cache.directory,
        cache_ttl_seconds=settings.asset_cache.ttl_seconds,
        gc_interval_seconds=settings.asset_cache.gc_interval_seconds,
    )
    context_assembler = providers.Singleton(
        ChatContextAssembler,
        message_repo=message_repo,
        session_repo=session_repo,
        hot_context_repo=hot_context_repo,
        oss_file_loader=oss_file_loader,
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
        bootstrap_servers=settings.messaging.kafka.bootstrap_servers,
    )

    context_compactor = providers.Singleton(
        DefaultLlmCompactor,
        text_llm=litellm_adapter,
        session_repo=session_repo,
        hot_context_repo=hot_context_repo,
    )
    chat_turn_stream_manager = providers.Singleton(
        ChatTurnStreamManager,
        stream_repo=chat_turn_stream_repo,
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
    inspect_tool_output_structure_tool = providers.Singleton(
        InspectToolOutputStructureTool,
        tool_content_store=tool_content_store,
    )
    read_tool_output_range_tool = providers.Singleton(
        ReadToolOutputRangeTool,
        tool_content_store=tool_content_store,
    )
    read_tool_output_section_tool = providers.Singleton(
        ReadToolOutputSectionTool,
        tool_content_store=tool_content_store,
    )
    search_tool_output_regex_tool = providers.Singleton(
        SearchToolOutputRegexTool,
        tool_content_store=tool_content_store,
    )
    search_tool_output_bm25_tool = providers.Singleton(
        SearchToolOutputBM25Tool,
        tool_content_store=tool_content_store,
    )
    # BYOK（Provider / Model 配置）
    list_my_providers_tool = providers.Singleton(ListMyProvidersTool, provider_repo=provider_repo)
    create_my_provider_tool = providers.Singleton(CreateMyProviderTool, provider_repo=provider_repo)
    update_my_provider_tool = providers.Singleton(UpdateMyProviderTool, provider_repo=provider_repo)
    delete_my_provider_tool = providers.Singleton(DeleteMyProviderTool, provider_repo=provider_repo)
    list_available_models_tool = providers.Singleton(ListAvailableModelsTool, model_repo=model_repo, provider_repo=provider_repo)
    create_my_model_tool = providers.Singleton(CreateMyModelTool, model_repo=model_repo)
    update_my_model_tool = providers.Singleton(UpdateMyModelTool, model_repo=model_repo)
    delete_my_model_tool = providers.Singleton(DeleteMyModelTool, model_repo=model_repo)
    bind_model_provider_tool = providers.Singleton(BindModelProviderTool, model_repo=model_repo)
    unbind_model_provider_tool = providers.Singleton(UnbindModelProviderTool, model_repo=model_repo)
    # Session
    list_sessions_tool = providers.Singleton(ListSessionsTool, session_repo=session_repo)
    rename_session_tool = providers.Singleton(RenameSessionTool, session_repo=session_repo)
    get_session_messages_tool = providers.Singleton(GetSessionMessagesTool, session_repo=session_repo, message_repo=message_repo)
    compact_session_tool = providers.Singleton(
        CompactSessionTool,
        session_repo=session_repo,
        agent_resolver=agent_resolver,
        context_compactor=context_compactor,
        context_assembler=context_assembler,
        turn_stream_manager=chat_turn_stream_manager,
    )
    # Memory
    search_memories_tool = providers.Singleton(SearchMemoriesTool, memory=memory_provider)
    add_memory_tool = providers.Singleton(AddMemoryTool, memory=memory_provider)
    list_memories_tool = providers.Singleton(ListMemoriesTool, memory=memory_provider)
    delete_memory_tool = providers.Singleton(DeleteMemoryTool, memory=memory_provider)
    tool_providers = providers.List(
        search_history_tool,
        load_image_attachment_tool,
        load_skill_tool,
        load_skill_asset_tool,
        inspect_tool_output_structure_tool,
        read_tool_output_range_tool,
        read_tool_output_section_tool,
        search_tool_output_regex_tool,
        search_tool_output_bm25_tool,
        list_my_providers_tool,
        create_my_provider_tool,
        update_my_provider_tool,
        delete_my_provider_tool,
        list_available_models_tool,
        create_my_model_tool,
        update_my_model_tool,
        delete_my_model_tool,
        bind_model_provider_tool,
        unbind_model_provider_tool,
        list_sessions_tool,
        rename_session_tool,
        get_session_messages_tool,
        compact_session_tool,
        search_memories_tool,
        add_memory_tool,
        list_memories_tool,
        delete_memory_tool,
    )

    tool_registry = providers.Singleton(
        _build_registry,
        tool_providers=tool_providers,
        tool_config_repo=tool_config_repo,
        mcp_tool_catalog=mcp_tool_catalog,
        system_mcp_tool_catalog=system_mcp_tool_catalog,
    )

    # Application 层组件
    frontend_tool_response_broker = providers.Singleton(
        FrontendToolResponseBroker,
        response_repo=frontend_tool_response_repo,
        kafka_producer=kafka_producer,
        timeout_seconds=settings.chat_turn.frontend_response_timeout_seconds,
    )
    frontend_tool_response_consumer = providers.Singleton(
        FrontendToolResponseConsumer,
        bootstrap_servers=settings.messaging.kafka.bootstrap_servers,
        service_name=bootstrap_settings.SERVICE_NAME,
        broker=frontend_tool_response_broker,
    )
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
        redirect_queue=chat_turn_queue_repo,
        agent_resolver=agent_resolver,
        context_assembler=context_assembler,
        frontend_tool_response_broker=frontend_tool_response_broker,
        context_compactor=context_compactor,
    )


# 全局容器实例
container = Container()
