from typing import Any

from chat.application.tools.core import (
    OutputCacheSpec,
    OutputCacheStrategy,
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolUISpec,
)
from chat.domain.entities.model import ModelScope
from chat.domain.repositories import ModelRepository


class ListAvailableModelsTool:
    def __init__(self, model_repo: ModelRepository) -> None:
        self._model_repo = model_repo
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="list_available_models",
                description=(
                    "List models available to the current user, including system and personal models "
                    "with their provider mappings. Use this before binding a model to a provider."
                ),
                parameters_schema=ToolParametersSchema({"type": "object", "properties": {}}),
            ),
            policy=ToolPolicy(
                category="model_management",
                expose_by_default=True,
                risk_level=ToolRiskLevel.LOW,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
            ),
            output_cache_spec=OutputCacheSpec(strategy=OutputCacheStrategy.ITEMS, cache_paths=("system_models", "user_models")),
            ui_spec=ToolUISpec(display_name="列出可用模型", description="查询当前用户可用的系统模型和个人模型。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        user_id = str(context.get("user_id") or "")
        system_infos = await self._model_repo.list_models_and_mappings(ModelScope.SYSTEM, user_id)
        user_infos = await self._model_repo.list_models_and_mappings(ModelScope.USER, user_id)

        def render(info) -> dict[str, Any]:
            return {
                "id": str(info.model.id),
                "name": info.model.display_name,
                "family": info.model.model_family.value,
                "mappings": [
                    {
                        "provider_id": str(m.provider_id),
                        "name": m.provider_model_name,
                        "preferred": m.is_preferred,
                    }
                    for m in info.mappings if m.is_active
                ],
            }

        return {
            "system_models": [render(i) for i in system_infos if i.model.is_active],
            "user_models": [render(i) for i in user_infos if i.model.is_active],
        }
