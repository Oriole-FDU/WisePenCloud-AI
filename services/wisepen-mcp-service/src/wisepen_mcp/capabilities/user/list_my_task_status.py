from __future__ import annotations

from typing import Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from wisepen_mcp.service_client import UserClient


def register_list_my_task_status_tool(mcp: FastMCP, user_client: UserClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="list_my_task_status",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="列出任务状态", description="列出当前用户的任务状态。"),
        failure_reason="Task Status List Failed",
    ))
    @mcp.tool(
        name="list_my_task_status",
        description="List the current user's task completion status and reward previews, including the daily check-in task.",
    )
    async def list_my_task_status() -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        data = await user_client.list_my_task_status()
        items = []
        for item in data:
            items.append({
                "task_code": item.task_code.name if item.task_code else None,
                "task_type": item.task_type.name if item.task_type else None,
                "enabled": item.enabled,
                "can_complete": item.can_complete,
                "reward_preview": item.reward_preview,
                "once": item.once,
                "periodic": item.periodic,
            })
        return {"items": items}


__all__ = ["register_list_my_task_status_tool"]
