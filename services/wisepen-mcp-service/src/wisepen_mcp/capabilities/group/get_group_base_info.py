from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.service_client import UserClient


def register_get_group_base_info_tool(mcp: FastMCP, user_client: UserClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="get_group_base_info",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="读取小组基本信息", description="读取指定小组的基本信息。"),
        failure_reason="Group Base Info Load Failed",
    ))
    @mcp.tool(
        name="get_group_base_info",
        description=(
            "Get basic display info for one Wisepen group by group id. Does not require the current user to be a "
            "member. Use list_group_members for member/role details, or the cloud disk tools to browse the group's files."
        ),
    )
    async def get_group_base_info(
        group_id: Annotated[str, Field(min_length=1, description="Group id returned by list_groups.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        group_id = group_id.strip()
        data = await user_client.get_group_base_info(group_id=group_id)
        return {
            "group_id": data.group_id,
            "group_name": data.group_name,
            "group_type": data.group_type.name if data.group_type else None,
            "group_desc": data.group_desc,
            "group_cover_url": data.group_cover_url,
            "owner_id": data.owner_id,
            "owner_info": data.owner_info,
            "member_count": data.member_count,
            "create_time": data.create_time,
        }


__all__ = ["register_get_group_base_info_tool"]
