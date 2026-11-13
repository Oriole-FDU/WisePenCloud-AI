from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import MountResourcesToGroupRequest
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient


def register_mount_resources_to_group_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @mcp.tool(
        name="mount_resources_to_group",
        description=(
            "Mount resources to a target folder/tag in a group space. group_id is the group id from list_groups and "
            "target_tag_id is a group folder/tag id from get_resource_tag_tree. The current user must be the resources' "
            "owner or the group OWNER/ADMIN."
        ),
    )
    async def mount_resources_to_group(
        resource_ids: Annotated[list[str], Field(min_length=1, description="Resource ids to mount.")],
        group_id: Annotated[str, Field(min_length=1, description="Group id from list_groups.")],
        target_tag_id: Annotated[
            str,
            Field(min_length=1, description="Target group folder/tag id from get_resource_tag_tree."),
        ],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resolved_resource_ids = [str(resource_id).strip() for resource_id in resource_ids if str(resource_id).strip()]
        if not resolved_resource_ids:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "resource_ids must not be empty.")
        group_id = group_id.strip()
        target_tag_id = target_tag_id.strip()

        data = await resource_client.mount_resources_to_group(
            request=MountResourcesToGroupRequest(
                resource_ids=resolved_resource_ids,
                group_id=group_id,
                target_tag_id=target_tag_id,
            ),
        )
        return {"resource_count": data.resource_count, "updated": True}


__all__ = ["register_mount_resources_to_group_tool"]
