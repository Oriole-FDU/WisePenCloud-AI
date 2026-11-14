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
from chat.core.config.app_settings import settings
from chat.domain.interfaces import MemoryProvider


class SearchMemoriesTool:
    def __init__(self, memory: MemoryProvider) -> None:
        self._memory = memory
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="search_memories",
                description="Semantically search the current user's long-term memories for relevant facts.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to search for."},
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.LOW,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
                max_output_chars=settings.tools.result_max_chars,
            ),
            ui_spec=ToolUISpec(display_name="检索长期记忆", description="按语义检索当前用户的长期记忆。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        query = str(kwargs.get("query") or "").strip()
        if not query:
            raise ToolExecutionError(reason="invalid_query", detail_reason="query is required.")
        limit = int(kwargs.get("limit") or 5)
        results = await self._memory.search(user_id, query, limit=limit)
        if not results:
            return "No matching memories found."
        return "Relevant memories:\n" + "\n".join(f"- {r}" for r in results)
