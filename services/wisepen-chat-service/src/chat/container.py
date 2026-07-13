# src/chat/container.py

from typing import List

from dependency_injector import containers, providers
from v2.nacos import NacosNamingService

from chat.application.agents import DefaultAgentResolver
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.llm_provider_resolver import LLMProviderResolver
from chat.application.token_counter import TokenCounter
from chat.application.tools.core import ToolRegistry
from chat.application.tools.note_tools import ApplyCurrentNoteAiDiffPlanTool, ReadNoteAixmlTool
from chat.application.tools.session_tools.get_historical_chat_messages_tool import GetHistoricalChatMessagesTool
from chat.application.tools.skill_tools import (
    CreateSkillInfoTool,
    GetSkillInfoTool,
    LoadSkillAssetTool,
    LoadSkillTool,
    UpdateSkillInfoTool,
    UploadSkillDraftAssetTool,
)
from chat.application.tools.skill_tools.utils.skill_matcher import DefaultSkillMatcher
from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.core.config.nacos import nacos_client_manager
from chat.core.persistence import (
    MongoMessageRepository,
    MongoModelRepository,
    MongoProviderRepository,
    MongoSessionRepository,
    MongoToolConfigRepository,
    RedisHotContext,
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
from chat.service_client import AIAssetClient, FileStorageClient, NoteCollabClient, ResourceClient
from common.cloud.service_discovery import ServiceDiscovery
from common.http.rpc_client import RpcClient
from common.kafka.producer import KafkaProducerClient


async def _provide_nacos_naming() -> NacosNamingService:
    """延迟到首次 await，避免在 import 阶段触发 async Nacos 建连。"""
    return await nacos_client_manager.get_naming_client()


def _build_registry(
    tool_providers: List[providers.Provider],
    tool_config_repo: ToolConfigRepository,
) -> ToolRegistry:
    """组装并返回已注册所有工具的 ToolRegistry 实例。"""
    registry = ToolRegistry(tool_config_repo=tool_config_repo)
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
    hot_context_repo = providers.Singleton(RedisHotContext)

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
    note_collab_client = providers.Singleton(
        NoteCollabClient,
        rpc=rpc_client,
        service_name=settings.NOTE_COLLAB_SERVICE_NAME,
        gateway_base_url=settings.NOTE_COLLAB_GATEWAY_BASE_URL,
        read_timeout_seconds=settings.NOTE_AI_DIFF_READ_TIMEOUT_SECONDS,
        apply_timeout_seconds=settings.NOTE_AI_DIFF_APPLY_TIMEOUT_SECONDS,
    )

    oss_file_loader = providers.Singleton(
        OssFileLoader,
        file_storage_client=file_storage_client,
        cache_dir=settings.OSS_CACHE_DIR,
        cache_ttl_seconds=settings.OSS_CACHE_TTL_SECONDS,
        gc_interval_seconds=settings.OSS_CACHE_GC_INTERVAL_SECONDS,
    )

    skill_matcher = providers.Singleton(
        DefaultSkillMatcher,
        ai_asset_client=ai_asset_client,
    )
    agent_resolver = providers.Singleton(DefaultAgentResolver)
    kafka_producer = providers.Singleton(
        KafkaProducerClient,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
    )

    search_history_tool = providers.Singleton(
        GetHistoricalChatMessagesTool,
        message_repo=message_repo,
    )
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
    read_note_aixml_tool = providers.Singleton(
        ReadNoteAixmlTool,
        note_collab_client=note_collab_client,
        max_xml_chars=settings.NOTE_AI_DIFF_MAX_XML_CHARS,
        timeout_seconds=settings.NOTE_AI_DIFF_READ_TIMEOUT_SECONDS,
    )
    apply_current_note_ai_diff_plan_tool = providers.Singleton(
        ApplyCurrentNoteAiDiffPlanTool,
        note_collab_client=note_collab_client,
        timeout_seconds=settings.NOTE_AI_DIFF_APPLY_TIMEOUT_SECONDS,
    )

    tool_providers = providers.List(
        search_history_tool,
        load_skill_tool,
        load_skill_asset_tool,
        create_skill_info_tool,
        get_skill_info_tool,
        update_skill_info_tool,
        upload_skill_draft_asset_tool,
        read_note_aixml_tool,
        apply_current_note_ai_diff_plan_tool,
    )

    tool_registry = providers.Singleton(
        _build_registry,
        tool_providers=tool_providers,
        tool_config_repo=tool_config_repo,
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
        kafka_producer=kafka_producer,
        skill_matcher=skill_matcher,
        agent_resolver=agent_resolver,
    )


container = Container()
