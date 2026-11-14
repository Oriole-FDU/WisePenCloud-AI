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
from chat.domain.entities.model import ModelScope
from chat.domain.repositories import ModelRepository


class DeleteMyModelTool:
    def __init__(self, model_repo: ModelRepository) -> None:
        self._model_repo = model_repo
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="delete_my_model",
                description="Delete a personal model definition owned by the current user.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "model_id": {"type": "string", "description": "The model id to delete."},
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
            ui_spec=ToolUISpec(display_name="删除我的模型", description="删除当前用户的个人模型定义。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        model_id = str(kwargs.get("model_id") or "").strip()
        if not model_id:
            raise ToolExecutionError(reason="invalid_model", detail_reason="model_id is required.")
        await self._model_repo.delete_model(PydanticObjectId(model_id), ModelScope.USER, user_id)
        return f"Model deleted: {model_id}"
