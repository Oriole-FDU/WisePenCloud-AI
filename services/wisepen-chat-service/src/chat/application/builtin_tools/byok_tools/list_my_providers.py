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
from chat.domain.entities.provider import ProviderScope
from chat.domain.repositories import ProviderRepository


class ListMyProvidersTool:
    def __init__(self, provider_repo: ProviderRepository) -> None:
        self._provider_repo = provider_repo
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="list_my_providers",
                description=(
                    "List the current user's personal LLM providers. "
                    "Use this to see configured providers and their ids before binding a model."
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
            output_cache_spec=OutputCacheSpec(strategy=OutputCacheStrategy.ITEMS, cache_paths=("items",)),
            ui_spec=ToolUISpec(display_name="列出我的 Provider", description="查看当前用户配置的个人 LLM Provider。"),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], config: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        user_id = str(context.get("user_id") or "")
        providers = await self._provider_repo.list_providers(ProviderScope.USER, user_id)
        return {
            "items": [
                {"id": str(p.id), "name": p.name, "type": p.type.value, "active": p.is_active}
                for p in providers
            ],
        }
