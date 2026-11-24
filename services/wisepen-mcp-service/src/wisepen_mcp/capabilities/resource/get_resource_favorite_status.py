from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.service_client import ResourceClient


def register_get_resource_favorite_status_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="get_resource_favorite_status",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="读取资源收藏状态", description="读取当前用户对指定资源的收藏状态。"),
        failure_reason="Resource Favorite Status Load Failed",
    ))
    @mcp.tool(
        name="get_resource_favorite_status",
        description=(
            "Get the current user's favorite status for one resource, as the list of collection ids it belongs to. "
            "A non-empty list means favorited. resource_id is from list_resources or search_resources."
        ),
    )
    async def get_resource_favorite_status(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        data = await resource_client.get_resource_favorite_status(resource_id=resource_id)
        return {"resource_id": resource_id, "collection_ids": data.collection_ids}


__all__ = ["register_get_resource_favorite_status_tool"]
