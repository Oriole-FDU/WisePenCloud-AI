from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import WalletBusinessType, WalletTransactionType
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import UserClient


def register_list_my_wallet_transactions_tool(mcp: FastMCP, user_client: UserClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="list_my_wallet_transactions",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="列出钱包流水", description="列出当前用户的钱包流水。"),
        failure_reason="Wallet Transaction List Failed",
    ))
    @mcp.tool(
        name="list_my_wallet_transactions",
        description=(
            "List wallet transaction records for the current user's personal wallet, or for a group wallet the user owns. "
            "Leave group_id empty for the personal wallet; pass a group_id (from list_groups) for a group wallet (requires OWNER). "
            "Optionally filter by transaction types and business type."
        ),
    )
    async def list_my_wallet_transactions(
        group_id: Annotated[
            str | None,
            Field(description="Optional group id from list_groups; empty means the personal wallet."),
        ] = None,
        wallet_transaction_types: Annotated[
            list[str] | None,
            Field(description="Optional transaction type filter. Allowed: REFILL, SPEND, TRANSFER_IN, TRANSFER_OUT, INCOME, EXCHANGE, REVERSE, GIFT, ONLY_RECORD_META."),
        ] = None,
        wallet_business_type: Annotated[
            str | None,
            Field(description="Optional business type filter. Allowed: TOKEN, COIN."),
        ] = None,
        page: Annotated[int, Field(ge=1, description="Page number starting from 1.")] = 1,
        size: Annotated[int, Field(ge=1, le=100, description="Page size.")] = 20,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resolved_group_id = group_id.strip() if group_id and group_id.strip() else None
        resolved_types: list[str] | None = None
        if wallet_transaction_types:
            resolved_types = []
            for value in wallet_transaction_types:
                try:
                    member = WalletTransactionType[str(value).strip().upper()]
                except KeyError:
                    raise ServiceException(McpErrorCode.USER_REQUEST_INVALID, f"unsupported wallet transaction type: {value}")
                if member.value not in resolved_types:
                    resolved_types.append(member.value)
        resolved_business_type: str | None = None
        if wallet_business_type:
            try:
                resolved_business_type = WalletBusinessType[wallet_business_type.strip().upper()].value
            except KeyError:
                raise ServiceException(McpErrorCode.USER_REQUEST_INVALID, "wallet_business_type must be TOKEN or COIN.")

        page_data = await user_client.list_my_wallet_transactions(
            group_id=resolved_group_id,
            wallet_transaction_types=resolved_types,
            wallet_business_type=resolved_business_type,
            page=page,
            size=size,
        )
        items = []
        for item in page_data.items:
            items.append({
                "trace_id": item.trace_id,
                "operator_id": item.operator_id,
                "count": item.count,
                "wallet_transaction_type": item.wallet_transaction_type.name if item.wallet_transaction_type else None,
                "wallet_business_type": item.wallet_business_type.name if item.wallet_business_type else None,
                "meta": item.meta,
                "billing_detail": item.billing_detail,
                "operator_display": item.operator_display,
                "create_time": item.create_time,
            })
        return {
            "group_id": resolved_group_id,
            "total": page_data.total,
            "items": items,
        }


__all__ = ["register_list_my_wallet_transactions_tool"]
