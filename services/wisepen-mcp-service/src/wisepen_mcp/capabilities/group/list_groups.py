from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import GroupType
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import UserClient


_GROUP_ROLE_FILTERS = frozenset({"ALL", "JOINED", "MANAGED"})


def register_list_groups_tool(mcp: FastMCP, user_client: UserClient) -> None:
    @mcp.tool(
        name="list_groups",
        description=(
            "List the current user's Wisepen collaboration groups, filtered by the user's role in each group. "
            "Returns group_id values that can be passed to other group tools (list_group_members, get_group_base_info) "
            "and to cloud disk tools (get_resource_tag_tree, list_resources, mount_resources_to_group, ...)."
        ),
    )
    async def list_groups(
        group_role_filter: Annotated[
            str,
            Field(description="Role filter. ALL: every group the user belongs to; JOINED: only groups where the user is a plain member; MANAGED: only groups where the user is OWNER or ADMIN."),
        ] = "ALL",
        group_type: Annotated[
            str | None,
            Field(description="Optional group type filter. Allowed: NORMAL_GROUP, ADVANCED_GROUP, MARKET_GROUP."),
        ] = None,
        page: Annotated[int, Field(ge=1, description="Page number starting from 1.")] = 1,
        size: Annotated[int, Field(ge=1, le=100, description="Page size.")] = 20,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        group_role_filter = str(group_role_filter or "").strip().upper()
        if group_role_filter not in _GROUP_ROLE_FILTERS:
            raise ServiceException(McpErrorCode.GROUP_REQUEST_INVALID, "group_role_filter must be one of ALL, JOINED, MANAGED.")

        resolved_group_type: str | None = None
        if group_type:
            try:
                resolved_group_type = GroupType[group_type.strip().upper()].name
            except KeyError:
                raise ServiceException(McpErrorCode.GROUP_REQUEST_INVALID, "group_type must be one of NORMAL_GROUP, ADVANCED_GROUP, MARKET_GROUP.")

        page_data = await user_client.list_groups(
            group_role_filter=group_role_filter,
            group_type=resolved_group_type,
            page=page,
            size=size,
        )
        items = []
        for item in page_data.items:
            items.append({
                "group_id": item.group_id,
                "group_name": item.group_name,
                "group_type": item.group_type.name if item.group_type else None,
                "group_desc": item.group_desc,
                "group_cover_url": item.group_cover_url,
                "owner_id": item.owner_id,
                "owner_info": item.owner_info,
                "member_count": item.member_count,
                "create_time": item.create_time,
            })
        return {
            "group_role_filter": group_role_filter,
            "group_type": resolved_group_type,
            "total": page_data.total,
            "items": items,
        }


__all__ = ["register_list_groups_tool"]
