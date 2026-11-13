from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import MoveResourcesInGroupRequest
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient


def register_move_resources_in_group_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="move_resources_in_group",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="小组内移动资源", description="在小组内移动资源。"),
        failure_reason="Resource Move Failed",
    ))
    @mcp.tool(
        name="move_resources_in_group",
        description=(
            "Move resources between folders/tags inside one group space. resource_source_tag_map maps each resource_id "
            "to its current source tag_id within the group (from list_resources tag_binds), and target_tag_id is the new "
            "group folder/tag id. The current user must be the resources' owner or the group OWNER/ADMIN."
        ),
    )
    async def move_resources_in_group(
        resource_source_tag_map: Annotated[
            dict[str, str],
            Field(description="Map of resource_id -> current source tag_id within the group."),
        ],
        group_id: Annotated[str, Field(min_length=1, description="Group id from list_groups.")],
        target_tag_id: Annotated[
            str,
            Field(min_length=1, description="Target group folder/tag id from get_resource_tag_tree."),
        ],
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
        target_tag_id = target_tag_id.strip()

        data = await resource_client.move_resources_in_group(
            request=MoveResourcesInGroupRequest(
                resource_source_tag_map=cleaned_map,
                group_id=group_id,
                target_tag_id=target_tag_id,
            ),
        )
        return {"resource_count": data.resource_count, "updated": True}


__all__ = ["register_move_resources_in_group_tool"]
