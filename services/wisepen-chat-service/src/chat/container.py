# src/chat/container.py

from dependency_injector import containers, providers
from typing import List
from chat.core.config.app_settings import settings

from chat.core.providers import LiteLLMAdapter, Mem0Adapter, LocalFSSkillAssetLoader
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
)
from common.kafka.producer import KafkaProducerClient


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

    # Skill 子系统：
    # - SkillRepository 只读 Mongo 里的 Skill 实体
    # - SkillAssetLoader 只读 Bundle 资产
    # 两者都是 Singleton，生命周期与进程一致
    skill_repo = providers.Singleton(MongoSkillRepository)
    skill_asset_loader = providers.Singleton(
        LocalFSSkillAssetLoader,
        root_dir=settings.SKILL_ASSETS_CACHE_DIR,
    )
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
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS
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

    tool_providers = providers.List(
        search_history_tool,
        load_skill_tool,
        load_skill_asset_tool,
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
