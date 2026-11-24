from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import CommentDeleteRequest
from wisepen_mcp.service_client import ResourceClient


def register_delete_comment_item_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="delete_comment_item",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.HIGH, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="删除评论/回复", description="删除一条评论或回复。"),
        failure_reason="Comment Delete Failed",
    ))
    @mcp.tool(
        name="delete_comment_item",
        description=(
            "Soft-delete a comment or reply the current user authored (admins or the resource owner may delete others'). "
            "resource_id is from list_resources/search_resources; comment_id is from list_comments or list_comment_replies."
        ),
    )
    async def delete_comment_item(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
        comment_id: Annotated[str, Field(min_length=1, description="Comment/reply id to delete.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        await resource_client.delete_comment_item(
            request=CommentDeleteRequest(resource_id=resource_id.strip(), comment_id=comment_id.strip()),
        )
        return {"comment_id": comment_id.strip(), "deleted": True}


__all__ = ["register_delete_comment_item_tool"]
