from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import AccessControlScope, ResourceAction, TagUpdateRequest
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient


def register_update_folder_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="update_folder",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="更新文件夹/标签", description="更新文件夹/标签名称或权限设置。"),
        failure_reason="Folder Update Failed",
    ))
    @mcp.tool(
        name="update_folder",
        description=(
            "Update a folder/tag node in a Wisepen resource space: optionally rename it and/or change its permission "
            "settings (resource ACL grant scope, mount permission scope, default granted actions). Leave group_id empty "
            "for the personal space, or pass a group_id (requires OWNER/ADMIN) for a group space. Personal-space tags "
            "cannot set visibility/permission settings."
        ),
    )
    async def update_folder(
        tag_id: Annotated[str, Field(min_length=1, description="Folder/tag id from get_resource_tag_tree.")],
        new_name: Annotated[
            str | None,
            Field(description="New folder/tag name; omit to keep the current name."),
        ] = None,
        group_id: Annotated[
            str | None,
            Field(description="Group id from list_groups; omit or pass empty for the personal space."),
        ] = None,
        granted_actions: Annotated[
            list[ResourceAction] | None,
            Field(description="Optional default actions granted to members matching this tag (group space only)."),
        ] = None,
        tagged_resource_acl_grant_scope: Annotated[
            AccessControlScope | None,
            Field(description="Optional resource ACL grant scope for this tag (group space only): ALL, ONLY_ADMIN, WHITELIST, BLACKLIST."),
        ] = None,
        tagged_resource_acl_grant_specified_users: Annotated[
            list[str] | None,
            Field(description="Optional user ids for WHITELIST/BLACKLIST resource ACL grant scope (group space only)."),
        ] = None,
        tag_mount_permission_scope: Annotated[
            AccessControlScope | None,
            Field(description="Optional mount permission scope for this tag (group space only): ALL, ONLY_ADMIN, WHITELIST, BLACKLIST."),
        ] = None,
        tag_mount_specified_users: Annotated[
            list[str] | None,
            Field(description="Optional user ids for WHITELIST/BLACKLIST mount permission scope (group space only)."),
        ] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        tag_id = tag_id.strip()
        new_name = new_name.strip() if new_name else None
        if new_name is not None and not new_name:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "new_name must not be blank.")
        resolved_group_id = group_id.strip() if group_id and group_id.strip() else None

        await resource_client.update_tag(
            request=TagUpdateRequest(
                group_id=resolved_group_id,
                target_tag_id=tag_id,
                tag_name=new_name,
                tagged_resource_acl_grant_scope=tagged_resource_acl_grant_scope,
                tagged_resource_acl_grant_specified_users=tagged_resource_acl_grant_specified_users,
                granted_actions=granted_actions,
                tag_mount_permission_scope=tag_mount_permission_scope,
                tag_mount_specified_users=tag_mount_specified_users,
            ),
        )
        return {"tag_id": tag_id, "new_name": new_name, "updated": True}


__all__ = ["register_update_folder_tool"]
