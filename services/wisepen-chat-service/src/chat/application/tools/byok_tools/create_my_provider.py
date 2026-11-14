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
from chat.domain.entities.provider import Provider, ProviderScope, ProviderType
from chat.domain.repositories import ProviderRepository

_PROVIDER_TYPES = [t.value for t in ProviderType]


class CreateMyProviderTool:
    def __init__(self, provider_repo: ProviderRepository) -> None:
        self._provider_repo = provider_repo
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="create_my_provider",
                description=(
                    "Create a new personal LLM provider for the current user. "
                    "The api_key is stored securely and is never returned in the result."
                ),
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Display name for the provider (unique per user)."},
                        "type": {"type": "string", "enum": _PROVIDER_TYPES, "description": "Provider type."},
                        "api_key": {"type": "string", "description": "API key for the provider."},
                        "base_url": {"type": "string", "description": "Optional custom gateway base URL."},
                        "is_active": {"type": "boolean", "default": True, "description": "Whether the provider is enabled."},
                    },
                    "required": ["name", "type", "api_key"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.MEDIUM,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
            ),
            ui_spec=ToolUISpec(display_name="创建我的 Provider", description="为当前用户新增一个个人 LLM Provider。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        name = str(kwargs.get("name") or "").strip()
        api_key = str(kwargs.get("api_key") or "").strip()
        if not name or not api_key:
            raise ToolExecutionError(reason="invalid_provider", detail_reason="name and api_key are required.")
        try:
            provider_type = ProviderType(str(kwargs.get("type")).strip())
        except ValueError:
            raise ToolExecutionError(reason="invalid_provider_type", detail_reason=f"type must be one of {_PROVIDER_TYPES}.")
        base_url = str(kwargs.get("base_url") or "").strip() or None
        is_active = bool(kwargs.get("is_active", True))
        provider = await self._provider_repo.create_provider(
            Provider(name=name, base_url=base_url, api_key=api_key, type=provider_type, is_active=is_active),
            scope=ProviderScope.USER,
            owner_user_id=user_id,
        )
        return f"Provider created: id={provider.id} name={provider.name} type={provider.type.value}."
