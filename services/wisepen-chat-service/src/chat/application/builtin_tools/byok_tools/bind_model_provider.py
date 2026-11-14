from decimal import Decimal
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
from chat.domain.repositories import ModelRepository


class BindModelProviderTool:
    def __init__(self, model_repo: ModelRepository) -> None:
        self._model_repo = model_repo
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="bind_model_provider",
                description="Bind a model to a personal provider with the provider-side model name.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "model_id": {"type": "string", "description": "Model id (system or personal)."},
                        "provider_id": {"type": "string", "description": "Personal provider id."},
                        "provider_model_name": {"type": "string", "description": "Provider-side model name, e.g. openai/gpt-4o."},
                        "is_preferred": {"type": "boolean", "default": True},
                        "is_active": {"type": "boolean", "default": True},
                    },
                    "required": ["model_id", "provider_id", "provider_model_name"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.MEDIUM,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
            ),
            ui_spec=ToolUISpec(display_name="绑定模型 Provider", description="为模型绑定一个个人 Provider 侧模型名。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        model_id = str(kwargs.get("model_id") or "").strip()
        provider_id = str(kwargs.get("provider_id") or "").strip()
        provider_model_name = str(kwargs.get("provider_model_name") or "").strip()
        if not model_id or not provider_id or not provider_model_name:
            raise ToolExecutionError(reason="invalid_binding", detail_reason="model_id, provider_id and provider_model_name are required.")
        mapping = await self._model_repo.bind_model_to_provider(
            PydanticObjectId(model_id),
            PydanticObjectId(provider_id),
            provider_model_name,
            only_system_model=False,
            owner_user_id=user_id,
            input_billing_ratio=Decimal("1"),
            cached_input_billing_ratio=Decimal("1"),
            output_billing_ratio=Decimal("1"),
            is_preferred=bool(kwargs.get("is_preferred", True)),
            is_active=bool(kwargs.get("is_active", True)),
        )
        return f"Model provider binding created: model_id={mapping.model_id} provider_id={mapping.provider_id} provider_model_name={mapping.provider_model_name}."
