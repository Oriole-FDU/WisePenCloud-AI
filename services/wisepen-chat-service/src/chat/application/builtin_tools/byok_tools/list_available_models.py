from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolUISpec,
)
from chat.core.config.app_settings import settings
from chat.domain.entities.model import ModelScope
from chat.domain.entities.provider import ProviderScope
from chat.domain.repositories import ModelRepository, ProviderRepository


class ListAvailableModelsTool:
    def __init__(self, model_repo: ModelRepository, provider_repo: ProviderRepository) -> None:
        self._model_repo = model_repo
        self._provider_repo = provider_repo
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
                expose_by_default=True,
                risk_level=ToolRiskLevel.LOW,
                persist_output=True,
                required_context_keys=("user_id",),
                timeout_seconds=10.0,
                max_output_chars=settings.tools.result_max_chars,
            ),
            ui_spec=ToolUISpec(display_name="列出可用模型", description="查询当前用户可用的系统模型和个人模型。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> str:
        user_id = str(context.get("user_id") or "")
        system_infos = await self._model_repo.list_models_and_mappings(ModelScope.SYSTEM, user_id)
        user_infos = await self._model_repo.list_models_and_mappings(ModelScope.USER, user_id)
        user_providers = await self._provider_repo.list_providers(ProviderScope.USER, user_id)

        def render(infos) -> str:
            lines = []
            for info in infos:
                if not info.model.is_active:
                    continue
                mappings = [
                    f"provider_id={m.provider_id} name={m.provider_model_name} preferred={m.is_preferred}"
                    for m in info.mappings if m.is_active
                ]
                line = f"- id={info.model.id} name={info.model.display_name} family={info.model.model_family.value}"
                if mappings:
                    line += " mappings=[" + "; ".join(mappings) + "]"
                lines.append(line)
            return "\n".join(lines) if lines else "(none)"

        user_provider_ids = {str(p.id) for p in user_providers}
        return (
            "Available models\nSystem:\n" + render(system_infos)
            + "\nPersonal:\n" + render(user_infos)
            + f"\nYour provider ids: {sorted(user_provider_ids) or '(none)'}"
        )
