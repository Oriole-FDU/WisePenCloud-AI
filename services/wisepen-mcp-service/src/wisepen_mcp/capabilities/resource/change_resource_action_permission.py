from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import ResourceAction, ResourceUpdateActionPermissionRequest
from wisepen_mcp.service_client import ResourceClient


def register_change_resource_action_permission_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="change_resource_action_permission",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.HIGH, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="调整资源权限", description="设置资源的组级覆盖权限和指定用户特权动作。"),
        failure_reason="Resource Permission Change Failed",
    ))
    @mcp.tool(
        name="change_resource_action_permission",
        description=(
            "Set resource-level action permission overrides for a resource the current user owns. "
            "override_granted_actions maps group_id -> list of ResourceAction values (omit to leave group overrides unchanged; "
            "pass null for a group's value to clear that group's override). specified_users_granted_actions maps user_id -> "
            "list of ResourceAction values (omit to clear all specified-user privileges). ResourceAction values: DISCOVER, VIEW, "
            "LOAD, EDIT, INLINE_COMMENT, DOWNLOAD_WATERMARK, DOWNLOAD_ORIGINAL, FORK, COMMENT, MARKET_VIEW."
        ),
    )
    async def change_resource_action_permission(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
        override_granted_actions: Annotated[
            dict[str, list[ResourceAction] | None] | None,
            Field(description="Optional group_id -> actions override map. Omit to keep existing group overrides."),
        ] = None,
        specified_users_granted_actions: Annotated[
            dict[str, list[ResourceAction]] | None,
            Field(description="Optional user_id -> actions privilege map. Omit to clear all specified-user privileges."),
        ] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        await resource_client.update_resource_action_permission(
            request=ResourceUpdateActionPermissionRequest(
                resource_id=resource_id,
                override_granted_actions=override_granted_actions,
                specified_users_granted_actions=specified_users_granted_actions,
            ),
        )
        return {"resource_id": resource_id, "updated": True}


__all__ = ["register_change_resource_action_permission_tool"]
