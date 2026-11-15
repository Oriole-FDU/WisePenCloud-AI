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
                expose_by_default=True,
                risk_level=ToolRiskLevel.LOW,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
            ),
            # ALWAYS_KEEP_RAW：像 list_resources 一样原样返回，不按 max_output_chars 截断
            output_cache_spec=OutputCacheSpec(strategy=OutputCacheStrategy.ALWAYS_KEEP_RAW),
            ui_spec=ToolUISpec(display_name="列出长期记忆", description="查看当前用户的长期记忆（最多 limit 条）。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        limit = max(1, int(kwargs.get("limit") or 50))
        # 多取一条判断是否还有更多
        items = await self._memory.get_all(user_id, limit=limit + 1)
        has_more = len(items) > limit
        items = items[:limit]
        if not items:
            return "No long-term memories yet."
        lines = [f"Long-term memories (showing up to {len(items)}):"]
        for item in items:
            lines.append(f"- id={item.get('id', '')} {item.get('memory', '')}")
        if has_more:
            lines.append("(more memories exist)")
        return "\n".join(lines)
