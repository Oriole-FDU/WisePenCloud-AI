from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import TagMoveRequest
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient


def register_move_folders_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @mcp.tool(
        name="move_folders",
        description=(
            "Move one or more folder/tag nodes under a new parent node in a Wisepen resource space. Leave group_id empty "
            "for the personal space, or pass a group_id (requires OWNER/ADMIN) for a group space. The new parent must have "
            "the same folder/tag type as the moved nodes."
        ),
    )
    async def move_folders(
        tag_ids: Annotated[list[str], Field(min_length=1, description="Folder/tag ids to move.")],
        new_parent_id: Annotated[
            str | None,
            Field(description="New parent node tag_id from get_resource_tag_tree; omit or pass empty to move to the root level."),
        ] = None,
        group_id: Annotated[
            str | None,
            Field(description="Group id from list_groups; omit or pass empty for the personal space."),
        ] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resolved_tag_ids = [str(tag_id).strip() for tag_id in tag_ids if str(tag_id).strip()]
        if not resolved_tag_ids:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "tag_ids must not be empty.")
        resolved_group_id = group_id.strip() if group_id and group_id.strip() else None
        resolved_new_parent_id = new_parent_id.strip() if new_parent_id and new_parent_id.strip() else None

        await resource_client.move_tags(
            request=TagMoveRequest(
                group_id=resolved_group_id,
                target_tag_ids=resolved_tag_ids,
                new_parent_id=resolved_new_parent_id,
            ),
        )
        return {
            "moved_tag_ids": resolved_tag_ids,
            "new_parent_id": resolved_new_parent_id,
            "updated": True,
        }


__all__ = ["register_move_folders_tool"]
