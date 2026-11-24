from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import FileOrganizationLogic, GroupResConfigUpdateRequest, ResourceAction
from wisepen_mcp.service_client import ResourceClient


def register_change_group_resource_config_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="change_group_resource_config",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.HIGH, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="设置小组资源配置", description="设置小组文件组织模式和默认成员动作权限。"),
        failure_reason="Group Resource Config Change Failed",
    ))
    @mcp.tool(
        name="change_group_resource_config",
        description=(
            "Set a Wisepen group's resource organization mode (FOLDER or TAG) and default member action permissions. "
            "The current user must be the group OWNER or ADMIN. ResourceAction values: DISCOVER, VIEW, LOAD, EDIT, "
            "INLINE_COMMENT, DOWNLOAD_WATERMARK, DOWNLOAD_ORIGINAL, FORK, COMMENT, MARKET_VIEW."
        ),
    )
    async def change_group_resource_config(
        group_id: Annotated[str, Field(min_length=1, description="Group id returned by list_groups.")],
        file_org_logic: Annotated[FileOrganizationLogic, Field(description="File organization mode: FOLDER or TAG.")],
        default_member_actions: Annotated[
            list[ResourceAction] | None,
            Field(description="Optional default member action permissions; omit to keep the current default."),
        ] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        group_id = group_id.strip()
        await resource_client.update_group_res_config(
            request=GroupResConfigUpdateRequest(
                group_id=group_id,
                file_org_logic=file_org_logic,
                default_member_actions=default_member_actions,
            ),
        )
        return {"group_id": group_id, "updated": True}


__all__ = ["register_change_group_resource_config_tool"]
