from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolUISpec,
)
from chat.domain.interfaces import MemoryProvider


class DeleteMemoryTool:
    def __init__(self, memory: MemoryProvider) -> None:
        self._memory = memory
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="delete_memory",
                description="Delete one long-term memory entry of the current user by id.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string", "description": "Memory id from list_memories."},
                    },
                    "required": ["memory_id"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.MEDIUM,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
            ),
            ui_spec=ToolUISpec(display_name="删除长期记忆", description="删除当前用户的一条长期记忆。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        memory_id = str(kwargs.get("memory_id") or "").strip()
        if not memory_id:
            raise ToolExecutionError(reason="invalid_memory", detail_reason="memory_id is required.")
        await self._memory.delete_memory(memory_id, user_id)
        return f"Memory deleted: {memory_id}"
