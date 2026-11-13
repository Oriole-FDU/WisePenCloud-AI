from __future__ import annotations

from typing import Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from wisepen_mcp.service_client import UserClient


def register_get_unread_message_count_tool(mcp: FastMCP, user_client: UserClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="get_unread_message_count",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="读取未读消息数", description="读取当前用户的未读消息数。"),
        failure_reason="Unread Message Count Load Failed",
    ))
    @mcp.tool(
        name="get_unread_message_count",
        description="Get the current user's unread in-app message count.",
    )
    async def get_unread_message_count() -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        count = await user_client.get_unread_message_count()
        return {"unread_count": count}


__all__ = ["register_get_unread_message_count_tool"]
