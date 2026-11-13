from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import GroupCreateRequest, GroupType
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import UserClient


def register_create_group_tool(mcp: FastMCP, user_client: UserClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="create_group",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="创建小组", description="创建新的 Wisepen 协作小组。"),
        failure_reason="Group Create Failed",
    ))
    @mcp.tool(
        name="create_group",
        description=(
            "Create a new Wisepen collaboration group owned by the current user, who becomes the group OWNER. "
            "The backend restricts which group types the current identity may create (students cannot create "
            "ADVANCED_GROUP, non-admins cannot create MARKET_GROUP). Returns the new group id."
        ),
    )
    async def create_group(
        group_name: Annotated[str, Field(min_length=1, description="Display name for the new group.")],
        group_type: Annotated[
            str,
            Field(description="Group type. Allowed: NORMAL_GROUP, ADVANCED_GROUP, MARKET_GROUP."),
        ],
        group_desc: Annotated[str, Field(min_length=1, description="Group description.")],
        group_cover_url: Annotated[str | None, Field(description="Optional group cover image URL.")] = None,
        group_meta_info: Annotated[str | None, Field(description="Optional group meta info; ignored for NORMAL_GROUP.")] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        group_name = group_name.strip()
        group_desc = group_desc.strip()
        if not group_name:
            raise ServiceException(McpErrorCode.GROUP_REQUEST_INVALID, "group_name must not be blank.")
        if not group_desc:
            raise ServiceException(McpErrorCode.GROUP_REQUEST_INVALID, "group_desc must not be blank.")
        try:
            group_type_enum = GroupType[group_type.strip().upper()]
        except KeyError:
            raise ServiceException(McpErrorCode.GROUP_REQUEST_INVALID, "group_type must be one of NORMAL_GROUP, ADVANCED_GROUP, MARKET_GROUP.")

        group_id = await user_client.create_group(
            request=GroupCreateRequest(
                group_name=group_name,
                group_type=group_type_enum,
                group_desc=group_desc,
                group_cover_url=group_cover_url,
                group_meta_info=group_meta_info,
            ),
        )
        return {
            "group_id": group_id,
            "group_type": group_type_enum.name,
            "group_name": group_name,
            "created": True,
        }


__all__ = ["register_create_group_tool"]
