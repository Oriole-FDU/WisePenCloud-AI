from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import ResourceRenameRequest
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient


def register_rename_resource_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @mcp.tool(
        name="rename_resource",
        description=(
            "Rename a resource owned by the current user. The new name is used in resource lists and search results. "
            "This does not change the resource type, file content, tags, or permissions."
        ),
    )
    async def rename_resource(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
        new_name: Annotated[str, Field(min_length=1, description="New resource name.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        new_name = new_name.strip()
        if not new_name:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "new_name must not be blank.")

        await resource_client.rename_resource(
            request=ResourceRenameRequest(resource_id=resource_id, new_name=new_name),
        )
        return {"resource_id": resource_id, "new_name": new_name, "updated": True}


__all__ = ["register_rename_resource_tool"]
