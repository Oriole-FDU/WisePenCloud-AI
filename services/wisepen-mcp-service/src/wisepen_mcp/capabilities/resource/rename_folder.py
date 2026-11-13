from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import TagUpdateRequest
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient


def register_rename_folder_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @mcp.tool(
        name="rename_folder",
        description=(
            "Rename a folder/tag node in a Wisepen resource space. Leave group_id empty for the personal space, or pass "
            "a group_id (requires OWNER/ADMIN) for a group space."
        ),
    )
    async def rename_folder(
        tag_id: Annotated[str, Field(min_length=1, description="Folder/tag id from get_resource_tag_tree.")],
        new_name: Annotated[str, Field(min_length=1, description="New folder/tag name.")],
        group_id: Annotated[
            str | None,
            Field(description="Group id from list_groups; omit or pass empty for the personal space."),
        ] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        tag_id = tag_id.strip()
        new_name = new_name.strip()
        if not new_name:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "new_name must not be blank.")
        resolved_group_id = group_id.strip() if group_id and group_id.strip() else None

        await resource_client.update_tag(
            request=TagUpdateRequest(group_id=resolved_group_id, target_tag_id=tag_id, tag_name=new_name),
        )
        return {"tag_id": tag_id, "new_name": new_name, "updated": True}


__all__ = ["register_rename_folder_tool"]
