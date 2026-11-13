from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.service_client import UserClient


def register_list_group_members_tool(mcp: FastMCP, user_client: UserClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="list_group_members",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="列出小组成员", description="列出指定小组的成员。"),
        failure_reason="Group Member List Failed",
    ))
    @mcp.tool(
        name="list_group_members",
        description=(
            "List the members of one Wisepen group the current user belongs to, with each member's role and, for "
            "advanced groups, token quota. group_id is the id returned by list_groups."
        ),
    )
    async def list_group_members(
        group_id: Annotated[str, Field(min_length=1, description="Group id returned by list_groups.")],
        page: Annotated[int, Field(ge=1, description="Page number starting from 1.")] = 1,
        size: Annotated[int, Field(ge=1, le=100, description="Page size.")] = 20,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        group_id = group_id.strip()
        page_data = await user_client.list_group_members(group_id=group_id, page=page, size=size)
        items = []
        for item in page_data.items:
            items.append({
                "group_id": item.group_id,
                "member_id": item.member_id,
                "role": item.role.name if item.role else None,
                "join_time": item.join_time,
                "token_limit": item.token_limit,
                "token_used": item.token_used,
                "member_info": item.member_info,
            })
        return {
            "group_id": group_id,
            "total": page_data.total,
            "items": items,
        }


__all__ = ["register_list_group_members_tool"]
