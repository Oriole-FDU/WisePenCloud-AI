from typing import Any

from chat.application.tools.core.definition import ToolParametersSchema, ToolPolicy
from chat.application.tools.core.execution.hooks.base import ToolPreflightHook, ToolPreflightResult
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.skill_tools.utils.builtin_skills import is_builtin_skill_id
from chat.service_client import ResourceClient
from common.security import SecurityContextHolder


class AllowedSkillIdCheck(ToolPreflightHook):
    name = "allowed_skill_id"

    async def check(
        self,
        invocation: ToolInvocation,
        policy: ToolPolicy,
        parameters_schema: ToolParametersSchema,
        context: dict[str, Any],
    ) -> ToolPreflightResult:
        skill_id = invocation.tool_call_arguments.get("skill_id")
        allowed_skill_ids = context.get("allowed_skill_ids") or []

        if skill_id not in allowed_skill_ids:
            return ToolPreflightResult(
                ok=False,
                message=f"Skill '{skill_id}' is not allowed in this turn.",
            )

        return ToolPreflightResult(ok=True)

class SkillPermissionCheck(ToolPreflightHook):

    def __init__(
        self,
        resource_client: ResourceClient,
    ) -> None:
        self._resource_client = resource_client

    async def check(
        self,
        invocation: ToolInvocation,
        policy: ToolPolicy,
        parameters_schema: ToolParametersSchema,
        context: dict[str, Any],
    ) -> ToolPreflightResult:
        skill_id = invocation.tool_call_arguments.get("skill_id")
        if is_builtin_skill_id(skill_id): # 内置 Skill 不需要鉴权
            return ToolPreflightResult(ok=True)

        skill_versions = context.get("skill_versions") or {}
        target_version = skill_versions.get(skill_id)
        if not isinstance(target_version, int) or target_version <= 0:
            target_version = None

        try:
            has_load_permission = await self._resource_client.has_load_permission(
                resource_id=skill_id,
                user_id=SecurityContextHolder.get_user_id(),
                group_role_map=SecurityContextHolder.get_group_role_map(),
                target_version=target_version,
            )
        except Exception as e:
            return ToolPreflightResult(ok=False, message=f"Failed to check permission for skill '{skill_id}'.")

        if has_load_permission:
            return ToolPreflightResult(ok=True)
        else:
            return ToolPreflightResult(ok=False, message=f"Permission denied for skill '{skill_id}'.")
