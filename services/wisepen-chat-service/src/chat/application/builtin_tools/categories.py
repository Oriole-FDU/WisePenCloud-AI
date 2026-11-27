from common.mcp import ToolCategoryMeta


CHAT_TOOL_CATEGORIES: tuple[ToolCategoryMeta, ...] = (
    ToolCategoryMeta(
        name="model_management",
        display_name="模型管理",
        description=(
            "Create, inspect, update, delete, bind, and unbind the current user's model and provider configurations."
        ),
    ),
    ToolCategoryMeta(
        name="memory_management",
        display_name="记忆管理",
        description="Explicitly search, inspect, add, and delete the current user's long-term memories.",
    ),
    ToolCategoryMeta(
        name="session_management",
        display_name="会话管理",
        description="List, rename, inspect, and compact the current user's chat sessions.",
    ),
)


__all__ = ["CHAT_TOOL_CATEGORIES"]
