from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolUISpec,
)
from chat.core.config.app_settings import settings
from chat.domain.interfaces import MemoryProvider


class ListMemoriesTool:
    def __init__(self, memory: MemoryProvider) -> None:
        self._memory = memory
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="list_memories",
                description="List all long-term memories of the current user.",
                parameters_schema=ToolParametersSchema({"type": "object", "properties": {}}),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.LOW,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
                max_output_chars=settings.tools.result_max_chars,
            ),
            ui_spec=ToolUISpec(display_name="列出长期记忆", description="查看当前用户的全部长期记忆。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        items = await self._memory.get_all(user_id)
        if not items:
            return "No long-term memories yet."
        lines = []
        for item in items:
            lines.append(f"- id={item.get('id', '')} {item.get('memory', '')}")
        return "Long-term memories:\n" + "\n".join(lines)
