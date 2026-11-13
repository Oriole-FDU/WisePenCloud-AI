from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.service_client import UserClient


def register_list_my_messages_tool(mcp: FastMCP, user_client: UserClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="list_my_messages",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="列出我的消息", description="列出当前用户的消息。"),
        failure_reason="Message List Failed",
    ))
    @mcp.tool(
        name="list_my_messages",
        description="List the current user's in-app messages, newest first. Includes both direct messages and visible system messages.",
    )
    async def list_my_messages(
        page: Annotated[int, Field(ge=1, description="Page number starting from 1.")] = 1,
        size: Annotated[int, Field(ge=1, le=100, description="Page size.")] = 20,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        page_data = await user_client.list_my_messages(page=page, size=size)
        items = []
        for item in page_data.items:
            items.append({
                "message_id": item.message_id,
                "delivery_scope": item.delivery_scope.name if item.delivery_scope else None,
                "message_type": item.message_type.name if item.message_type else None,
                "title": item.title,
                "content": item.content,
                "jump_url": item.jump_url,
                "extra": item.extra,
                "read_time": item.read_time,
                "create_time": item.create_time,
            })
        return {
            "total": page_data.total,
            "items": items,
        }


__all__ = ["register_list_my_messages_tool"]
