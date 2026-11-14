from __future__ import annotations

from typing import Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from wisepen_mcp.service_client import UserClient


def register_get_my_wallet_info_tool(mcp: FastMCP, user_client: UserClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="get_my_wallet_info",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="读取我的钱包信息", description="读取当前用户的钱包信息。"),
        failure_reason="Wallet Info Load Failed",
    ))
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
