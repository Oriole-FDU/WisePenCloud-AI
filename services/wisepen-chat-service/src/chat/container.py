# src/chat/container.py

from typing import List

from dependency_injector import containers, providers
from v2.nacos import NacosNamingService

from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.core.providers import (
    LiteLLMAdapter,
    Mem0Adapter,
    NullMemoryAdapter,
    LocalFSSkillAssetLoader,
    OssSkillAssetLoader,
)
from chat.core.providers.sandbox import AioGatewayProvider
from chat.core.persistence import (
    MongoSessionRepository,
    MongoMessageRepository,
    MongoSkillRepository,
    MongoModelRepository,
    MongoProviderRepository,
    RedisHotContext,
)
from chat.application.attachment_service import AttachmentService
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.skill_matcher import KeywordSkillMatcher
from chat.application.skill_cache_refresher import SkillCacheRefresher
from chat.application.tools import (
    ToolRegistry,
    SearchHistoricalMessagesTool,
    LoadSkillTool,
    LoadSkillAssetTool,
    ReadTextAttachmentTool,
    ReadPdfAttachmentTool,
    ReadWordAttachmentTool,
    ReadPptAttachmentTool,
    ReadExcelAttachmentTool,
    RunSandboxScriptTool,
    ReadFileTool,
    WriteFileTool,
)
from common.clients.file_storage import FileStorageClient
from chat.core.config.nacos import nacos_client_manager
from common.cloud.service_discovery import ServiceDiscovery
from common.http.rpc_client import RpcClient
from common.kafka.producer import KafkaProducerClient
from chat.core.kafka import FileUploadedConsumer


async def _provide_nacos_naming() -> NacosNamingService:
    """延迟到首次 await，避免在 import 阶段触发 async Nacos 建连。"""
    return await nacos_client_manager.get_naming_client()


def _build_registry(tool_providers: List[providers.Provider]) -> ToolRegistry:
    """工厂函数：组装并返回已注册所有工具的 ToolRegistry 实例。"""
    registry = ToolRegistry()
    for provider in tool_providers:
        registry.register(provider)
    return registry


class Container(containers.DeclarativeContainer):
    """依赖注入容器，管理单例对象的生命周期。"""
    llm_provider = providers.Singleton(LiteLLMAdapter)

    if (settings.QDRANT_HOST or "").strip().lower() in ("", "memory"):
        memory_provider = providers.Singleton(NullMemoryAdapter)
    else:
        memory_provider = providers.Singleton(Mem0Adapter)

    session_repo = providers.Singleton(MongoSessionRepository)
    message_repo = providers.Singleton(MongoMessageRepository)
    model_repo = providers.Singleton(MongoModelRepository)
    provider_repo = providers.Singleton(MongoProviderRepository)
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
    # 文件存储服务
    file_storage_client = providers.Singleton(
        FileStorageClient,
        rpc=rpc_client,
    )

    # Skill 子系统：
    # - SkillRepository 只读 Mongo 里的 Skill 实体
    # - SkillAssetLoader：DEV=True 用 LocalFS+OSS 回退；DEV=False 直连裸 OSS
    skill_repo = providers.Singleton(MongoSkillRepository)
    oss_skill_asset_loader = providers.Singleton(
        OssSkillAssetLoader,
        file_storage_client=file_storage_client,
        cache_dir=settings.SKILL_OSS_CACHE_DIR,
        cache_ttl_seconds=settings.SKILL_OSS_CACHE_TTL_SECONDS,
        gc_interval_seconds=settings.SKILL_OSS_CACHE_GC_INTERVAL_SECONDS,
    )
    # 开发态（profile=dev）使用 LocalFSSkillAssetLoader
    # 生产态（profile=prod）使用 OssSkillAssetLoader
    if bootstrap_settings.IS_DEV:
        skill_asset_loader = providers.Singleton(
            LocalFSSkillAssetLoader,
            root_dir=str(settings.SKILL_ASSETS_CACHE_PATH),
            oss_fallback=oss_skill_asset_loader,
        )
    else:
        skill_asset_loader = oss_skill_asset_loader
    # KeywordSkillMatcher
    skill_matcher = providers.Singleton(
        KeywordSkillMatcher,
        skill_repo=skill_repo,
    )
    # SkillCacheRefresher
    skill_cache_refresher = providers.Singleton(
        SkillCacheRefresher,
        matcher=skill_matcher,
        ttl_seconds=settings.SKILL_CACHE_TTL_SECONDS,
    )

    kafka_producer = providers.Singleton(
        KafkaProducerClient,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
    )

    kafka_consumer = providers.Singleton(
        FileUploadedConsumer,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        topic="wisepen-storage-file-uploaded-topic",
        group_id="wisepen-chat-upload-callback-group",
        session_repo=session_repo,
    )

    # 工具层：各 Tool 和 ToolRegistry 均为 Singleton，由容器统一管理生命周期
    search_history_tool = providers.Singleton(
        SearchHistoricalMessagesTool,
        message_repo=message_repo,
    )
    load_skill_tool = providers.Singleton(
        LoadSkillTool,
        skill_repo=skill_repo,
    )
    load_skill_asset_tool = providers.Singleton(
        LoadSkillAssetTool,
        skill_repo=skill_repo,
        skill_asset_loader=skill_asset_loader,
    )

    # 沙箱脚本执行工具
    run_sandbox_script_tool = providers.Singleton(
        RunSandboxScriptTool,
        base_url=settings.SANDBOX_BASE_URL,
        from_source=settings.SANDBOX_FROM_SOURCE,
    )

    # AIO Gateway 文件操作工具
    aio_gateway_provider = providers.Singleton(
        AioGatewayProvider,
        base_url=settings.AIO_GATEWAY_URL,
        from_source=settings.FROM_SOURCE_SECRET,
    )
    read_file_tool = providers.Singleton(
        ReadFileTool,
        aio_gateway=aio_gateway_provider,
    )
    write_file_tool = providers.Singleton(
        WriteFileTool,
        aio_gateway=aio_gateway_provider,
    )

    # Attachment reading tools — 使用 SessionRepository 鉴权附件归属
    read_text_attachment_tool = providers.Singleton(
        ReadTextAttachmentTool,
        file_storage_client=file_storage_client,
        session_repo=session_repo,
    )
    read_pdf_attachment_tool = providers.Singleton(
        ReadPdfAttachmentTool,
        file_storage_client=file_storage_client,
        session_repo=session_repo,
    )
    read_word_attachment_tool = providers.Singleton(
        ReadWordAttachmentTool,
        file_storage_client=file_storage_client,
        session_repo=session_repo,
    )
    read_ppt_attachment_tool = providers.Singleton(
        ReadPptAttachmentTool,
        file_storage_client=file_storage_client,
        session_repo=session_repo,
    )
    read_excel_attachment_tool = providers.Singleton(
        ReadExcelAttachmentTool,
        file_storage_client=file_storage_client,
        session_repo=session_repo,
    )

    tool_providers = providers.List(
        search_history_tool,
        load_skill_tool,
        load_skill_asset_tool,
        run_sandbox_script_tool,
        read_file_tool,
        write_file_tool,
        read_text_attachment_tool,
        read_pdf_attachment_tool,
        read_word_attachment_tool,
        read_ppt_attachment_tool,
        read_excel_attachment_tool,
    )

    tool_registry = providers.Singleton(
        _build_registry,
        tool_providers=tool_providers,
    )

    attachment_service = providers.Factory(
        AttachmentService,
        file_storage_client=file_storage_client,
        session_repo=session_repo,
    )

    # Application 层组件
    chat_turn_coordinator = providers.Factory(
        ChatTurnCoordinator,
        llm=llm_provider,
        memory=memory_provider,
        model_repo=model_repo,
        provider_repo=provider_repo,
        session_repo=session_repo,
        message_repo=message_repo,
        hot_context_repo=hot_context_repo,
        tool_registry=tool_registry,
        kafka_producer=kafka_producer,
        skill_matcher=skill_matcher,
    )


# 全局容器实例
container = Container()
