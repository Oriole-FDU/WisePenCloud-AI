from __future__ import annotations

import re
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


_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class SkillInfoValidationError(ValueError):
    pass


class UpdateSkillInfoTool:
    def __init__(self, ai_asset_client: AIAssetClient) -> None:
        self._ai_asset_client = ai_asset_client
        parameters_schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "resource_id": {
                    "type": "string",
                    "description": "Existing Wisepen Skill resource id.",
                },
                "name": {
                    "type": "string",
                    "description": "Skill frontmatter name. Must be lowercase hyphen-case.",
                },
                "description": {
                    "type": "string",
                    "description": "Skill trigger description. Should match /SKILL.md frontmatter description.",
                },
            },
            "required": ["resource_id", "name", "description"],
            "additionalProperties": False,
        }
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="update_skill_info",
                description=(
                    "Update the name and description fields of an existing Wisepen AIAsset Skill info record. "
                    "Use this only when the existing Skill metadata should change. "
                    "Do not use this to read Skill info; use get_skill_info instead. "
                    "This tool does not upload assets or publish."
                ),
                parameters_schema=ToolParametersSchema(parameters_schema),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                persist_output=True,
                risk_level=ToolRiskLevel.HIGH,
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
        try:
            resource_id = str(kwargs.get("resource_id"))
            name = _required_text(kwargs.get("name"), "name")
            description = _required_text(kwargs.get("description"), "description")
            if not _NAME_RE.fullmatch(name):
                raise SkillInfoValidationError(
                    "name must be lowercase hyphen-case using letters, digits, and single hyphens; "
                    "it must not start or end with a hyphen."
                )
        except SkillInfoValidationError as e:
            raise ToolExecutionError(
                reason="Skill Info Validation Failed",
                detail_reason=str(e),
                retryable=False,
            ) from e

        try:
            await self._ai_asset_client.update_skill_info(resource_id, name, description)
        except Exception as e:
            raise ToolExecutionError(
                reason="Skill Info Update Failed",
                detail_reason=str(e),
                retryable=False,
            ) from e

        return f"[Updated Skill Info] resource_id={resource_id} name={name} description={description}"


def _required_text(value: Any, field_name: str) -> str:
    value = value.strip()
    if not value:
        raise SkillInfoValidationError(f"{field_name} must not be blank.")
    return value
