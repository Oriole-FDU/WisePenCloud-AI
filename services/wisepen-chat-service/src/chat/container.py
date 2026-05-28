# src/chat/container.py

from dependency_injector import containers, providers

from chat.core.config.app_settings import settings
from chat.core.providers import LiteLLMAdapter, Mem0Adapter, NullMemoryAdapter
from chat.core.persistence import MongoSessionRepository, MongoMessageRepository, RedisHotContext
from chat.application.chat_orchestrator import ChatOrchestrator
from chat.application.tools import ToolRegistry, SearchHistoricalMessagesTool, RunSandboxScriptTool


def _build_registry(
    search_history_tool: SearchHistoricalMessagesTool,
    run_sandbox_tool: RunSandboxScriptTool,
) -> ToolRegistry:
    """工厂函数：组装并返回已注册所有工具的 ToolRegistry 实例。"""
    registry = ToolRegistry()
    registry.register(search_history_tool)
    registry.register(run_sandbox_tool)
    return registry


class Container(containers.DeclarativeContainer):
    """依赖注入容器，管理单例对象的生命周期。"""
    llm_provider = providers.Singleton(LiteLLMAdapter)

    if (settings.QDRANT_HOST or "").strip().lower() in ("", "memory"):
        memory_provider = providers.Singleton(NullMemoryAdapter)
    else:
        memory_provider = providers.Singleton(Mem0Adapter or NullMemoryAdapter)

    session_repo = providers.Singleton(MongoSessionRepository)
    message_repo = providers.Singleton(MongoMessageRepository)
    hot_context_repo = providers.Singleton(RedisHotContext)

    # 工具层：各 Tool 和 ToolRegistry 均为 Singleton，由容器统一管理生命周期
    search_history_tool = providers.Singleton(
        SearchHistoricalMessagesTool,
        message_repo=message_repo,
    )
    run_sandbox_tool = providers.Singleton(
        RunSandboxScriptTool,
        base_url=settings.SANDBOX_BASE_URL,
        from_source=settings.SANDBOX_FROM_SOURCE,
    )
    tool_registry = providers.Singleton(
        _build_registry,
        search_history_tool=search_history_tool,
        run_sandbox_tool=run_sandbox_tool,
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
    )


# 全局容器实例
container = Container()
