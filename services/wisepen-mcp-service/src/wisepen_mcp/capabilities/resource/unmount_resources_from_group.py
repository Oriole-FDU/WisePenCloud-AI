from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import UnmountResourcesFromGroupRequest
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient


def register_unmount_resources_from_group_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @mcp.tool(
        name="unmount_resources_from_group",
        description=(
            "Remove resources from a group folder/tag (unmount). resource_source_tag_map maps each resource_id to its "
            "current source tag_id within the group (from list_resources tag_binds). The current user must be the "
            "resources' owner or the group OWNER/ADMIN."
        ),
    )
    async def unmount_resources_from_group(
        resource_source_tag_map: Annotated[
            dict[str, str],
            Field(description="Map of resource_id -> current source tag_id within the group."),
        ],
        group_id: Annotated[str, Field(min_length=1, description="Group id from list_groups.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        cleaned_map = {
            str(resource_id).strip(): str(source_tag_id).strip()
            for resource_id, source_tag_id in (resource_source_tag_map or {}).items()
            if str(resource_id).strip() and str(source_tag_id).strip()
        }
        if not cleaned_map:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "resource_source_tag_map must not be empty.")
        group_id = group_id.strip()

        data = await resource_client.unmount_resources_from_group(
            request=UnmountResourcesFromGroupRequest(
                resource_source_tag_map=cleaned_map,
                group_id=group_id,
            ),
        )
        return {"resource_count": data.resource_count, "updated": True}


__all__ = ["register_unmount_resources_from_group_tool"]
