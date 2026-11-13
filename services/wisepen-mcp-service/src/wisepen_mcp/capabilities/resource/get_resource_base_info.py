from __future__ import annotations

from typing import Annotated, Any

from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.service_client import ResourceClient


def register_get_resource_base_info_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @mcp.tool(
        name="get_resource_base_info",
        description=(
            "Get basic display info for one current-user-visible Wisepen resource (name, type, owner, size, preview, "
            "interaction stats). Use list_resources or search_resources to obtain a resource_id first."
        ),
    )
    async def get_resource_base_info(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        data = await resource_client.get_resource_base_info(resource_id=resource_id)
        return {
            "resource_id": data.resource_id,
            "resource_name": data.resource_name,
            "resource_type": data.resource_type.name if data.resource_type else None,
            "owner_id": data.owner_id,
            "preview": data.preview,
            "size": data.size,
            "create_time": data.create_time,
            "owner_info": data.owner_info,
            "resource_interaction_info": data.resource_interaction_info,
        }


__all__ = ["register_get_resource_base_info_tool"]
