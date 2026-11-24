from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.service_client import ResourceClient


def register_get_resource_user_interaction_record_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="get_resource_user_interaction_record",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="读取资源互动状态", description="读取当前用户对资源的阅读、点赞和评分记录。"),
        failure_reason="Resource Interaction Record Load Failed",
    ))
    @mcp.tool(
        name="get_resource_user_interaction_record",
        description=(
            "Get the current user's read / liked / scored record (and liked comment ids) for one resource. "
            "resource_id is from list_resources or search_resources."
        ),
    )
    async def get_resource_user_interaction_record(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        data = await resource_client.get_resource_user_interaction_record(resource_id=resource_id)
        return {
            "resource_id": data.resource_id,
            "read": data.read,
            "liked": data.liked,
            "score": data.score,
            "liked_comment_ids": data.liked_comment_ids,
        }


__all__ = ["register_get_resource_user_interaction_record_tool"]
