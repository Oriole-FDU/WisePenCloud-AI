from __future__ import annotations

from typing import Any

from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from wisepen_mcp.service_client import UserClient


def register_get_my_wallet_info_tool(mcp: FastMCP, user_client: UserClient) -> None:
    @mcp.tool(
        name="get_my_wallet_info",
        description=(
            "Read the current user's personal wallet balance: used token points, available token points, and coin balance."
        ),
    )
    async def get_my_wallet_info() -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        data = await user_client.get_my_wallet_info()
        return {
            "token_used": data.token_used,
            "token_balance": data.token_balance,
            "coin_balance": data.coin_balance,
        }


__all__ = ["register_get_my_wallet_info_tool"]
