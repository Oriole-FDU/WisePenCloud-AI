from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import CommentCreateRequest
from wisepen_mcp.service_client import ResourceClient


def register_create_comment_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="create_comment",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="发布评论", description="在资源下发布一条顶级评论。"),
        failure_reason="Comment Create Failed",
    ))
    @mcp.tool(
        name="create_comment",
        description=(
            "Create a top-level comment on a Wisepen resource the current user can view. "
            "resource_id is from list_resources or search_resources."
        ),
    )
    async def create_comment(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
        content: Annotated[str, Field(min_length=1, description="Comment text.")],
        image_urls: Annotated[list[str] | None, Field(description="Optional image URL list.")] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        comment_id = await resource_client.create_comment(
            request=CommentCreateRequest(
                resource_id=resource_id.strip(),
                content=content,
                image_urls=image_urls or [],
            ),
        )
        return {"comment_id": comment_id, "created": True}


__all__ = ["register_create_comment_tool"]
