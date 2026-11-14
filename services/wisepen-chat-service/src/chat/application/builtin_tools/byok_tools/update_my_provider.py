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
from chat.domain.entities.provider import ProviderScope, ProviderType
from chat.domain.repositories import ProviderRepository

_PROVIDER_TYPES = [t.value for t in ProviderType]


class UpdateMyProviderTool:
    def __init__(self, provider_repo: ProviderRepository) -> None:
        self._provider_repo = provider_repo
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="update_my_provider",
                description="Update fields of an existing personal LLM provider. Only pass the fields to change.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "provider_id": {"type": "string", "description": "The provider id to update."},
                        "name": {"type": "string", "description": "Optional new display name."},
                        "base_url": {"type": "string", "description": "Optional new base URL."},
                        "api_key": {"type": "string", "description": "Optional new API key."},
                        "type": {"type": "string", "enum": _PROVIDER_TYPES, "description": "Optional new type."},
                        "is_active": {"type": "boolean", "description": "Optional enable/disable."},
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
            ui_spec=ToolUISpec(display_name="更新我的 Provider", description="维护当前用户的个人 LLM Provider 配置。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        provider_id = str(kwargs.get("provider_id") or "").strip()
        if not provider_id:
            raise ToolExecutionError(reason="invalid_provider", detail_reason="provider_id is required.")
        updates: dict[str, Any] = {}
        if kwargs.get("name") is not None:
            updates["name"] = str(kwargs["name"]).strip()
        if kwargs.get("base_url") is not None:
            updates["base_url"] = str(kwargs["base_url"]).strip() or None
        if kwargs.get("api_key") is not None:
            updates["api_key"] = str(kwargs["api_key"]).strip()
        if kwargs.get("type") is not None:
            try:
                updates["type"] = ProviderType(str(kwargs["type"]).strip())
            except ValueError:
                raise ToolExecutionError(reason="invalid_provider_type", detail_reason=f"type must be one of {_PROVIDER_TYPES}.")
        if kwargs.get("is_active") is not None:
            updates["is_active"] = bool(kwargs["is_active"])
        if not updates:
            raise ToolExecutionError(reason="no_updates", detail_reason="No fields to update were provided.")
        provider = await self._provider_repo.update_provider(
            PydanticObjectId(provider_id), updates, ProviderScope.USER, user_id
        )
        return f"Provider updated: id={provider.id} name={provider.name}."
