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


class AddMemoryTool:
    def __init__(self, memory: MemoryProvider) -> None:
        self._memory = memory
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="add_memory",
                description=(
                    "Record a fact or preference about the current user into long-term memory. "
                    "Only record facts the user explicitly stated or clearly wants remembered long-term. "
                    "Phrase content as a third-person statement, e.g. '用户偏好使用中文回复'."
                ),
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The fact or preference to remember, as a third-person statement."},
                    },
                    "required": ["content"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.MEDIUM,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
            ),
            ui_spec=ToolUISpec(display_name="写入长期记忆", description="把用户明确表达、值得长期保留的事实写入记忆。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        content = str(kwargs.get("content") or "").strip()
        if not content:
            raise ToolExecutionError(reason="invalid_content", detail_reason="content is required.")
        await self._memory.add_memory(user_id, content)
        return "Memory saved."
