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


class UnbindModelProviderTool:
    def __init__(self, model_repo: ModelRepository) -> None:
        self._model_repo = model_repo
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="unbind_model_provider",
                description="Unbind a model from a personal provider.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "model_id": {"type": "string", "description": "Model id."},
                        "provider_id": {"type": "string", "description": "Personal provider id."},
                    },
                    "required": ["model_id", "provider_id"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.MEDIUM,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
            ),
            ui_spec=ToolUISpec(display_name="解绑模型 Provider", description="解除模型与个人 Provider 的绑定。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        model_id = str(kwargs.get("model_id") or "").strip()
        provider_id = str(kwargs.get("provider_id") or "").strip()
        if not model_id or not provider_id:
            raise ToolExecutionError(reason="invalid_binding", detail_reason="model_id and provider_id are required.")
        await self._model_repo.unbind_model_from_provider(
            PydanticObjectId(model_id),
            PydanticObjectId(provider_id),
            only_system_model=False,
            owner_user_id=user_id,
        )
        return f"Model provider binding removed: model_id={model_id} provider_id={provider_id}."
