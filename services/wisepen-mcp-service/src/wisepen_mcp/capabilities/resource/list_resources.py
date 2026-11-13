from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient


_SORT_BY_VALUES = frozenset({"UPDATE_TIME", "CREATE_TIME", "NAME", "SIZE"})
_SORT_DIR_VALUES = frozenset({"ASC", "DESC"})
_QUERY_LOGIC_VALUES = frozenset({"AND", "OR"})


def register_list_resources_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="list_resources",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="列出资源", description="列出个人或小组的资源。"),
        failure_reason="Resource List Failed",
    ))
    @mcp.tool(
        name="list_resources",
        description=(
            "List the current user's visible resources in a Wisepen resource space, optionally filtered by folder/tag. "
            "Leave group_id empty for the personal space, or pass a group_id (from list_groups) for a group space. "
            "Use get_resource_tag_tree to obtain folder tag_ids. Returned resource_id values can be passed to "
            "get_resource_base_info, rename_resource, and the resource placement tools."
        ),
    )
    async def list_resources(
        group_id: Annotated[
            str | None,
            Field(description="Group id from list_groups; omit or pass empty for the personal space."),
        ] = None,
        tag_ids: Annotated[
            list[str] | None,
            Field(description="Optional folder/tag id filter; empty means all visible resources in the space."),
        ] = None,
        resource_type: Annotated[
            str | None,
            Field(description="Optional resource type filter, e.g. NOTE, PDF, DOC, DOCX, PPT, PPTX, XLS, XLSX."),
        ] = None,
        tag_query_logic_mode: Annotated[str, Field(description="Multi-tag logic: AND or OR.")] = "OR",
        include_my_interaction: Annotated[
            bool,
            Field(description="Whether to include the current user's interaction record per resource."),
        ] = False,
        sort_by: Annotated[str, Field(description="Sort field: UPDATE_TIME, CREATE_TIME, NAME, or SIZE.")] = "UPDATE_TIME",
        sort_dir: Annotated[str, Field(description="Sort direction: ASC or DESC.")] = "DESC",
        page: Annotated[int, Field(ge=1, description="Page number starting from 1.")] = 1,
        size: Annotated[int, Field(ge=1, le=100, description="Page size.")] = 20,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resolved_group_id = group_id.strip() if group_id and group_id.strip() else None
        tag_query_logic_mode = tag_query_logic_mode.strip().upper()
        sort_by = sort_by.strip().upper()
        sort_dir = sort_dir.strip().upper()
        if tag_query_logic_mode not in _QUERY_LOGIC_VALUES:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "tag_query_logic_mode must be AND or OR.")
        if sort_by not in _SORT_BY_VALUES:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "sort_by must be one of UPDATE_TIME, CREATE_TIME, NAME, SIZE.")
        if sort_dir not in _SORT_DIR_VALUES:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "sort_dir must be ASC or DESC.")
        resolved_tag_ids = [str(tag_id).strip() for tag_id in (tag_ids or []) if str(tag_id).strip()]
        resolved_resource_type = resource_type.strip().upper() if resource_type and resource_type.strip() else None

        page_data = await resource_client.list_resources(
            group_id=resolved_group_id,
            tag_ids=resolved_tag_ids or None,
            tag_query_logic_mode=tag_query_logic_mode,
            include_my_interaction=include_my_interaction,
            resource_type=resolved_resource_type,
            page=page,
            size=size,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
        items = []
        for item in page_data.items:
            items.append({
                "resource_id": item.resource_id,
                "resource_name": item.resource_name,
                "resource_type": item.resource_type.name if item.resource_type else None,
                "owner_id": item.owner_id,
                "preview": item.preview,
                "size": item.size,
                "create_time": item.create_time,
                "current_actions": item.current_actions,
                "tag_binds": item.tag_binds,
                "my_interaction_record": item.my_interaction_record,
                "owner_info": item.owner_info,
            })
        return {
            "group_id": resolved_group_id,
            "tag_ids": resolved_tag_ids or None,
            "resource_type": resolved_resource_type,
            "total": page_data.total,
            "items": items,
        }


__all__ = ["register_list_resources_tool"]
