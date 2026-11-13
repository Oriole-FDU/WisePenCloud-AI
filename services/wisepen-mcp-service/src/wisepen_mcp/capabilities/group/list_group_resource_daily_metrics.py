from __future__ import annotations

import re
from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import UserClient


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def register_list_group_resource_daily_metrics_tool(mcp: FastMCP, user_client: UserClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="list_group_resource_daily_metrics",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=20.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="小组资源每日指标", description="列出指定小组的资源每日指标。"),
        failure_reason="Group Resource Daily Metrics Load Failed",
    ))
    @mcp.tool(
        name="list_group_resource_daily_metrics",
        description=(
            "Get per-day group activity metrics (resource added, read, like, comment, AI call counts) for one group "
            "over a date range. The current user must be the group OWNER or ADMIN. Dates use yyyy-MM-dd."
        ),
    )
    async def list_group_resource_daily_metrics(
        group_id: Annotated[str, Field(min_length=1, description="Group id returned by list_groups.")],
        start_date: Annotated[str, Field(min_length=1, description="Start date, yyyy-MM-dd (inclusive).")],
        end_date: Annotated[str, Field(min_length=1, description="End date, yyyy-MM-dd (inclusive).")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        group_id = group_id.strip()
        start_date = start_date.strip()
        end_date = end_date.strip()
        if not group_id:
            raise ServiceException(McpErrorCode.GROUP_REQUEST_INVALID, "group_id must not be blank.")
        if not _DATE_RE.fullmatch(start_date) or not _DATE_RE.fullmatch(end_date):
            raise ServiceException(McpErrorCode.GROUP_REQUEST_INVALID, "start_date and end_date must use yyyy-MM-dd.")

        data = await user_client.list_group_resource_daily_metrics(
            group_id=group_id,
            start_date=start_date,
            end_date=end_date,
        )
        items = []
        for item in data:
            items.append({
                "stat_date": item.stat_date,
                "resource_added_count": item.resource_added_count,
                "resource_read_count": item.resource_read_count,
                "resource_like_count": item.resource_like_count,
                "resource_comment_count": item.resource_comment_count,
                "resource_ai_call_count": item.resource_ai_call_count,
            })
        return {
            "group_id": group_id,
            "start_date": start_date,
            "end_date": end_date,
            "items": items,
        }


__all__ = ["register_list_group_resource_daily_metrics_tool"]
