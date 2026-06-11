from typing import Any

from chat.application.tools.core.definition import ToolParametersSchema, ToolPolicy
from chat.application.tools.core.execution.hooks.base import ToolPreflightHook, ToolPreflightResult
from chat.application.tools.core.llm.invocation import ToolInvocation
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
        try:
            res_check_permission_res = await self._resource_client.check_res_permission(
                resource_id=skill_id,
                user_id=SecurityContextHolder.get_user_id(),
                group_role_map=SecurityContextHolder.get_group_role_map(),
            )
        except Exception as e:
            return ToolPreflightResult(ok=False, message=f"Failed to check permission for skill '{skill_id}'.")

        allowed_actions = res_check_permission_res.get("allowedActions") or []
        if "VIEW" in allowed_actions:
            return ToolPreflightResult(ok=True)
        else:
            return ToolPreflightResult(ok=False, message=f"Permission denied for skill '{skill_id}'.")
