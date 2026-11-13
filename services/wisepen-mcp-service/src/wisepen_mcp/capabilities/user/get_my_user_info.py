from __future__ import annotations

from typing import Any

from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from wisepen_mcp.service_client import UserClient


def register_get_my_user_info_tool(mcp: FastMCP, user_client: UserClient) -> None:
    @mcp.tool(
        name="get_my_user_info",
        description=(
            "Read the current user's account info, profile info, and the read-only field list. "
            "Takes no parameters; the target user is the current authenticated user."
        ),
    )
    async def get_my_user_info() -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        data = await user_client.get_my_user_info()
        return {
            "user_id": data.user_id,
            "user_info": data.user_info,
            "user_profile": data.user_profile,
            "readonly_fields": data.readonly_fields,
        }


__all__ = ["register_get_my_user_info_tool"]
