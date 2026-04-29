# src/chat/container.py

from typing import List

from dependency_injector import containers, providers
from v2.nacos import NacosNamingService

from chat.core.config.app_settings import settings
from chat.core.config.bootstrap_settings import bootstrap_settings
from chat.core.providers import (
    LiteLLMAdapter,
    Mem0Adapter,
    LocalFSSkillAssetLoader,
    OssSkillAssetLoader,
)
from chat.core.persistence import (
    MongoSessionRepository,
    MongoMessageRepository,
    MongoSkillRepository,
    RedisHotContext,
)
from chat.application.model_resolver import ModelResolver
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.skill_matcher import KeywordSkillMatcher
from chat.application.skill_cache_refresher import SkillCacheRefresher
from chat.application.tools import (
    ToolRegistry,
    SearchHistoricalMessagesTool,
    LoadSkillTool,
    LoadSkillAssetTool,
    WebSearchTool,
    BrowseUrlTool,
    BrowseInteractTool,
)
from common.clients.file_storage import FileStorageClient
from common.cloud.nacos_client import nacos_client_manager
from common.cloud.service_discovery import ServiceDiscovery
from common.http.rpc_client import RpcClient
from common.kafka.producer import KafkaProducerClient


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
    memory_provider = providers.Singleton(Mem0Adapter)

    session_repo = providers.Singleton(MongoSessionRepository)
    message_repo = providers.Singleton(MongoMessageRepository)
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

    # Skill 子系统：
    # - SkillRepository 只读 Mongo 里的 Skill 实体
    # - SkillAssetLoader：DEV=True 用 LocalFS+OSS 回退；DEV=False 直连裸 OSS
    skill_repo = providers.Singleton(MongoSkillRepository)
    oss_skill_asset_loader = providers.Singleton(
        OssSkillAssetLoader,
        file_storage_client=file_storage_client,
        cache_dir=settings.skill_oss_cache_path,
        cache_ttl_seconds=settings.SKILL_OSS_CACHE_TTL_SECONDS,
        gc_interval_seconds=settings.SKILL_OSS_CACHE_GC_INTERVAL_SECONDS,
    )
    # 开发态使用 LocalFSSkillAssetLoader
    # 线上态使用 OssSkillAssetLoader
    if settings.DEV:
        skill_asset_loader = providers.Singleton(
            LocalFSSkillAssetLoader,
            root_dir=str(settings.skill_assets_cache_path),
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

    # 工具层：各 Tool 和 ToolRegistry 均为 Singleton，由容器统一管理生命周期
    # SearchHistoricalMessagesTool
    search_history_tool = providers.Singleton(
        SearchHistoricalMessagesTool,
        message_repo=message_repo,
    )
    # LoadSkillTool / LoadSkillAssetTool
    load_skill_tool = providers.Singleton(
        LoadSkillTool,
        skill_repo=skill_repo,
    )
    load_skill_asset_tool = providers.Singleton(
        LoadSkillAssetTool,
        skill_repo=skill_repo,
        skill_asset_loader=skill_asset_loader,
    )
    # WebSearchTool
    web_search_tool = providers.Singleton(
        WebSearchTool,
    )
    # BrowseUrlTool
    browse_url_tool = providers.Singleton(
        BrowseUrlTool,
    )
    # BrowseInteractTool
    browse_interact_tool = providers.Singleton(
        BrowseInteractTool,
    )

    tool_providers = providers.List(
        search_history_tool,
        load_skill_tool,
        load_skill_asset_tool,
        web_search_tool,
        browse_url_tool,
        browse_interact_tool,
    )

    tool_registry = providers.Singleton(
        _build_registry,
        tool_providers=tool_providers,
    )

    model_resolver = providers.Singleton(ModelResolver)

    # Application 层组件
    chat_turn_coordinator = providers.Factory(
        ChatTurnCoordinator,
        llm=llm_provider,
        memory=memory_provider,
        model_resolver=model_resolver,
        session_repo=session_repo,
        message_repo=message_repo,
        hot_context_repo=hot_context_repo,
        tool_registry=tool_registry,
        kafka_producer=kafka_producer,
        skill_matcher=skill_matcher,
    )


# 全局容器实例
container = Container()
