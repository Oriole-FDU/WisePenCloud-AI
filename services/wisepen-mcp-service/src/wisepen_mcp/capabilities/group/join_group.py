from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import UserClient


def register_join_group_tool(mcp: FastMCP, user_client: UserClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="join_group",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="加入小组", description="通过邀请码加入小组。"),
        failure_reason="Group Join Failed",
    ))
    @mcp.tool(
        name="join_group",
        description=(
            "Join an existing Wisepen group using its invite code. The current user must not already belong to the "
            "group. After joining, the user becomes a plain MEMBER."
        ),
    )
    async def join_group(
        invite_code: Annotated[str, Field(min_length=1, description="Group invite code.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        invite_code = invite_code.strip()
        if not invite_code:
            raise ServiceException(McpErrorCode.GROUP_REQUEST_INVALID, "invite_code must not be blank.")

        await user_client.join_group(invite_code=invite_code)
        return {"invite_code": invite_code, "joined": True}


__all__ = ["register_join_group_tool"]
