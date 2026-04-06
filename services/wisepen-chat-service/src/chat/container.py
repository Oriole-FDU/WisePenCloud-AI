# src/chat/container.py

from dependency_injector import containers, providers
from typing import List
from chat.core.config.app_settings import settings

from chat.core.providers import LiteLLMAdapter, Mem0Adapter
from chat.core.persistence import MongoSessionRepository, MongoMessageRepository, RedisHotContext
from chat.application.chat_orchestrator import ChatOrchestrator
from chat.application.tools import ToolRegistry, SearchHistoricalMessagesTool
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

    kafka_producer = providers.Singleton(
        KafkaProducerClient, 
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS
    )

    # 工具层：各 Tool 和 ToolRegistry 均为 Singleton，由容器统一管理生命周期
    search_history_tool = providers.Singleton(
        SearchHistoricalMessagesTool,
        message_repo=message_repo,
    )

    tool_providers = providers.List(
        search_history_tool,
    )

    tool_registry = providers.Singleton(
        _build_registry,
        tool_providers=tool_providers,
    )

    # Application 层组件
    chat_service = providers.Factory(
        ChatOrchestrator,
        llm=llm_provider,
        memory=memory_provider,
        session_repo=session_repo,
        message_repo=message_repo,
        hot_context_repo=hot_context_repo,
        tool_registry=tool_registry,
        kafka_producer=kafka_producer,
    )


# 全局容器实例
container = Container()
