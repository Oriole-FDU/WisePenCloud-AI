from typing import Any

from beanie import PydanticObjectId

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolUISpec,
)
from chat.domain.entities.provider import ProviderScope
from chat.domain.repositories import ProviderRepository


class DeleteMyProviderTool:
    def __init__(self, provider_repo: ProviderRepository) -> None:
        self._provider_repo = provider_repo
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="delete_my_provider",
                description="Delete a personal LLM provider owned by the current user.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "provider_id": {"type": "string", "description": "The provider id to delete."},
                    },
                    "required": ["provider_id"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.MEDIUM,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
            ),
            ui_spec=ToolUISpec(display_name="删除我的 Provider", description="删除当前用户的个人 LLM Provider。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        provider_id = str(kwargs.get("provider_id") or "").strip()
        if not provider_id:
            raise ToolExecutionError(reason="invalid_provider", detail_reason="provider_id is required.")
        await self._provider_repo.remove_provider(PydanticObjectId(provider_id), ProviderScope.USER, user_id)
        return f"Provider deleted: {provider_id}"
