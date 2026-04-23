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

    # Skill 子系统（chat-service 作为只读消费方）：
    # - SkillRepository 只读 Mongo 里的 Skill 实体（写入由 Java 微服务负责）
    # - SkillAssetLoader 只读 Bundle 资产（生产形态是 OSS，本轮用本地目录作为缓存/fixture）
    # 两者都是 Singleton，生命周期与进程一致；路径从 app_settings 读
    skill_repo = providers.Singleton(MongoSkillRepository)
    skill_asset_loader = providers.Singleton(
        LocalFSSkillAssetLoader,
        root_dir=settings.SKILL_ASSETS_CACHE_DIR,
    )
    # KeywordSkillMatcher 内部 cache 由 SkillCacheRefresher 管理（startup 首刷 + TTL 周期刷新）
    skill_matcher = providers.Singleton(
        KeywordSkillMatcher,
        skill_repo=skill_repo,
    )
    # SkillCacheRefresher：唯一的 Skill cache 生命周期入口
    # - lifespan startup 调 start()：内部先 eager trigger()，再挂起 TTL 循环
    # - lifespan shutdown 调 stop()：回收后台 task
    # - 未来 Kafka consumer 可复用 trigger() 做事件驱动刷新
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
    search_history_tool = providers.Singleton(
        SearchHistoricalMessagesTool,
        message_repo=message_repo,
    )
    # LoadSkillTool / LoadSkillAssetTool 和普通业务工具一起注册进 Registry，
    # 但它们 reserved=True，默认隐藏；只有当 SkillMatcher 本轮命中时，
    # ChatTurnCoordinator 通过 ToolRegistry.derive(expose={...}) 显式解禁它们。
    # 如此 Registry 成为"全量工具的唯一权威来源"，无需分叉静态 extras 路径。
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
