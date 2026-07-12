from __future__ import annotations

from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.core.config.app_settings import settings
from chat.service_client import AIAssetClient


class SkillInfoValidationError(ValueError):
    pass


class GetSkillInfoTool:
    def __init__(self, ai_asset_client: AIAssetClient) -> None:
        self._ai_asset_client = ai_asset_client
        parameters_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "resource_id": {
                    "type": "string",
                    "description": "Existing Wisepen Skill resource id.",
                },
            },
            "required": ["resource_id"],
            "additionalProperties": False,
        }
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="get_skill_info",
                description=(
                    "Read the Skill info record for an existing Wisepen AIAsset Skill by resource_id. "
                    "Use this when updating an existing Skill or when you need its current name, description, source_type, version, and next draft_version. "
                    "This tool does not create, update, upload assets, or publish."
                ),
                parameters_schema=ToolParametersSchema(parameters_schema),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("allowed_skill_ids",),
                required_allowed_builtin_skill_ids=("builtin:skill-creator",),
                timeout_seconds=15.0,
                max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
            ),
            preflight_hooks=(),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        resource_id = str(kwargs.get("resource_id"))
        try:
            skill_info = await self._ai_asset_client.get_skill_info(resource_id)
            if skill_info is None or not skill_info.resource_id:
                raise ToolExecutionError(
                    reason="Skill Info Not Found",
                    detail_reason=f"Skill '{resource_id}' was not returned by AIAsset.",
                    retryable=False,
                )
        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(
                reason="Skill Info Load Failed",
                detail_reason=str(e),
                retryable=False,
            ) from e

        return (
            f"[Loaded Skill Info] resource_id={skill_info.resource_id} name={skill_info.name} "
            f"description={skill_info.description} source_type={skill_info.source_type} "
            f"version={skill_info.version} draft_version={skill_info.version + 1} "
        )
