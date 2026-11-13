from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import TagCreateRequest
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient


def register_create_folder_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @mcp.tool(
        name="create_folder",
        description=(
            "Create a new folder/tag node under a parent node in a Wisepen resource space. The new node inherits the "
            "parent's type, so pass a folder (is_path=true) tag_id from get_resource_tag_tree as parent_id to create a "
            "folder. Leave group_id empty for the personal space, or pass a group_id (requires OWNER/ADMIN) for a group space."
        ),
    )
    async def create_folder(
        parent_id: Annotated[
            str,
            Field(min_length=1, description="Parent node tag_id from get_resource_tag_tree (use a folder node to create a folder)."),
        ],
        name: Annotated[str, Field(min_length=1, description="New folder/tag name.")],
        group_id: Annotated[
            str | None,
            Field(description="Group id from list_groups; omit or pass empty for the personal space."),
        ] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        name = name.strip()
        parent_id = parent_id.strip()
        if not name:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "name must not be blank.")
        if not parent_id:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "parent_id must not be blank.")
        resolved_group_id = group_id.strip() if group_id and group_id.strip() else None

        tag_id = await resource_client.create_tag(
            request=TagCreateRequest(group_id=resolved_group_id, parent_id=parent_id, tag_name=name),
        )
        return {
            "tag_id": tag_id,
            "name": name,
            "group_id": resolved_group_id,
            "created": True,
        }


__all__ = ["register_create_folder_tool"]
