from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolUISpec,
)
from chat.domain.interfaces import MemoryProvider


class DeleteAllMemoriesTool:
    def __init__(self, memory: MemoryProvider) -> None:
        self._memory = memory
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="delete_all_memories",
                description=(
                    "Delete ALL long-term memories of the current user. "
                    "This is irreversible; only call it when the user explicitly asks to clear all memories."
                ),
                parameters_schema=ToolParametersSchema({"type": "object", "properties": {}}),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.MEDIUM,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
            ),
            ui_spec=ToolUISpec(display_name="删除全部长期记忆", description="清空当前用户的全部长期记忆。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        await self._memory.delete_all_for_user(user_id)
        return "All long-term memories deleted."
