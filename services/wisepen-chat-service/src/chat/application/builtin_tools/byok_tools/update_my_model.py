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
from chat.domain.entities.model import ModelFamily, ModelScope
from chat.domain.repositories import ModelRepository

_MODEL_FAMILIES = [f.value for f in ModelFamily]


class UpdateMyModelTool:
    def __init__(self, model_repo: ModelRepository) -> None:
        self._model_repo = model_repo
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="update_my_model",
                description="Update fields of an existing personal model definition. Only pass the fields to change.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "model_id": {"type": "string", "description": "The model id to update."},
                        "display_name": {"type": "string"},
                        "model_family": {"type": "string", "enum": _MODEL_FAMILIES},
                        "support_thinking": {"type": "boolean"},
                        "support_vision": {"type": "boolean"},
                        "support_tools": {"type": "boolean"},
                        "context_window_tokens": {"type": "integer"},
                        "max_output_tokens": {"type": "integer"},
                        "is_active": {"type": "boolean"},
                    },
                    "required": ["model_id"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.MEDIUM,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
            ),
            ui_spec=ToolUISpec(display_name="更新我的模型", description="维护当前用户的个人模型定义。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        model_id = str(kwargs.get("model_id") or "").strip()
        if not model_id:
            raise ToolExecutionError(reason="invalid_model", detail_reason="model_id is required.")
        updates: dict[str, Any] = {}
        for key in ("display_name", "context_window_tokens", "max_output_tokens", "is_active", "support_thinking", "support_vision", "support_tools"):
            if kwargs.get(key) is not None:
                updates[key] = kwargs[key]
        if kwargs.get("model_family") is not None:
            try:
                updates["model_family"] = ModelFamily(str(kwargs["model_family"]).strip())
            except ValueError:
                raise ToolExecutionError(reason="invalid_model_family", detail_reason=f"model_family must be one of {_MODEL_FAMILIES}.")
        if not updates:
            raise ToolExecutionError(reason="no_updates", detail_reason="No fields to update were provided.")
        model = await self._model_repo.update_model(PydanticObjectId(model_id), updates, ModelScope.USER, user_id)
        return f"Model updated: id={model.id} name={model.display_name}."
