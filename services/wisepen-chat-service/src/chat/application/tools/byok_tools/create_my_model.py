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
from chat.domain.entities.model import Model, ModelFamily, ModelScope
from chat.domain.repositories import ModelRepository

_MODEL_FAMILIES = [f.value for f in ModelFamily]


class CreateMyModelTool:
    def __init__(self, model_repo: ModelRepository) -> None:
        self._model_repo = model_repo
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="create_my_model",
                description="Create a new personal model definition for the current user.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "display_name": {"type": "string", "description": "Display name (unique per user), e.g. GPT-4o."},
                        "model_family": {"type": "string", "enum": _MODEL_FAMILIES, "default": "GENERIC"},
                        "support_thinking": {"type": "boolean", "default": False},
                        "support_vision": {"type": "boolean", "default": False},
                        "support_tools": {"type": "boolean", "default": True},
                        "context_window_tokens": {"type": "integer", "description": "Optional context window token limit."},
                        "max_output_tokens": {"type": "integer", "description": "Optional max output tokens."},
                    },
                    "required": ["display_name"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.MEDIUM,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
            ),
            ui_spec=ToolUISpec(display_name="创建我的模型", description="为当前用户新增一个个人模型定义。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        display_name = str(kwargs.get("display_name") or "").strip()
        if not display_name:
            raise ToolExecutionError(reason="invalid_model", detail_reason="display_name is required.")
        try:
            family = ModelFamily(str(kwargs.get("model_family") or "GENERIC").strip())
        except ValueError:
            raise ToolExecutionError(reason="invalid_model_family", detail_reason=f"model_family must be one of {_MODEL_FAMILIES}.")
        model = await self._model_repo.create_model(
            Model(
                display_name=display_name,
                model_family=family,
                support_thinking=bool(kwargs.get("support_thinking", False)),
                support_vision=bool(kwargs.get("support_vision", False)),
                support_tools=bool(kwargs.get("support_tools", True)),
                context_window_tokens=kwargs.get("context_window_tokens"),
                max_output_tokens=kwargs.get("max_output_tokens"),
            ),
            scope=ModelScope.USER,
            owner_user_id=user_id,
        )
        return f"Model created: id={model.id} name={model.display_name} family={model.model_family.value}."
