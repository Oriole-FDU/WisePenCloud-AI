from typing import Any

from chat.application.tools.core import (
    OutputCacheSpec,
    OutputCacheStrategy,
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolUISpec,
)
from chat.domain.interfaces import MemoryProvider


class ListMemoriesTool:
    def __init__(self, memory: MemoryProvider) -> None:
        self._memory = memory
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="list_memories",
                description="List up to limit of the current user's long-term memories (default 50).",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "default": 50},
                    },
                }),
            ),
            policy=ToolPolicy(
                category="memory_management",
                expose_by_default=True,
                risk_level=ToolRiskLevel.LOW,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
            ),
            output_cache_spec=OutputCacheSpec(strategy=OutputCacheStrategy.ITEMS, cache_paths=("items",)),
            ui_spec=ToolUISpec(display_name="列出长期记忆", description="查看当前用户的长期记忆（最多 limit 条）。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        user_id = str(context.get("user_id") or "")
        limit = max(1, int(kwargs.get("limit") or 50))
        # 多取一条判断是否还有更多
        items = await self._memory.get_all(user_id, limit=limit + 1)
        has_more = len(items) > limit
        items = items[:limit]
        return {
            "items": [
                {"id": str(item.get("id", "")), "memory": item.get("memory", "")}
                for item in items
            ],
            "has_more": has_more,
        }
