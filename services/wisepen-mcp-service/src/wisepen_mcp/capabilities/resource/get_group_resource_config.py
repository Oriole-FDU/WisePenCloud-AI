from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.service_client import ResourceClient


def register_get_group_resource_config_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="get_group_resource_config",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="读取小组资源配置", description="查询小组的文件组织模式和默认成员动作权限。"),
        failure_reason="Group Resource Config Load Failed",
    ))
    @mcp.tool(
        name="get_group_resource_config",
        description=(
            "Get a Wisepen group's resource organization mode (FOLDER or TAG) and its default member action permissions. "
            "The current user must be a member of the group. group_id is the id returned by list_groups."
        ),
    )
    async def get_group_resource_config(
        group_id: Annotated[str, Field(min_length=1, description="Group id returned by list_groups.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        group_id = group_id.strip()
        data = await resource_client.get_group_res_config(group_id=group_id)
        return {
            "group_id": data.group_id,
            "file_org_logic": data.file_org_logic,
            "default_member_actions": data.default_member_actions,
        }


__all__ = ["register_get_group_resource_config_tool"]
