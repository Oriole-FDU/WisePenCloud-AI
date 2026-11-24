from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import TagDeleteRequest
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient


def register_remove_folders_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="remove_folders",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.HIGH, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="删除文件夹/标签", description="级联删除文件夹/标签及其所有子孙节点。"),
        failure_reason="Folder Delete Failed",
    ))
    @mcp.tool(
        name="remove_folders",
        description=(
            "Delete one or more folder/tag nodes and all their descendant nodes in a Wisepen resource space. "
            "This is a cascade delete: deleting a path tag may also trigger soft-delete flows for resources bound only to it. "
            "Leave group_id empty for the personal space, or pass a group_id (requires OWNER/ADMIN) for a group space. "
            "System root and trash nodes cannot be deleted."
        ),
    )
    async def remove_folders(
        tag_ids: Annotated[list[str], Field(min_length=1, description="Folder/tag ids from get_resource_tag_tree to delete.")],
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

        await resource_client.remove_tags(
            request=TagDeleteRequest(group_id=resolved_group_id, target_tag_ids=resolved_tag_ids),
        )
        return {"removed_tag_ids": resolved_tag_ids, "updated": True}


__all__ = ["register_remove_folders_tool"]
