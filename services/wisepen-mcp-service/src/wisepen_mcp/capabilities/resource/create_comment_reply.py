from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import CommentReplyCreateRequest
from wisepen_mcp.service_client import ResourceClient


def register_create_comment_reply_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="create_comment_reply",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="回复评论", description="回复一条顶级评论或另一条回复。"),
        failure_reason="Comment Reply Create Failed",
    ))
    @mcp.tool(
        name="create_comment_reply",
        description=(
            "Create a reply to a top-level comment or another reply on a Wisepen resource. "
            "reply_to is the comment_id of the target comment/reply, from list_comments or list_comment_replies."
        ),
    )
    async def create_comment_reply(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
        reply_to: Annotated[str, Field(min_length=1, description="Target comment id to reply to.")],
        content: Annotated[str, Field(min_length=1, description="Reply text.")],
        image_urls: Annotated[list[str] | None, Field(description="Optional image URL list.")] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        comment_id = await resource_client.create_comment_reply(
            request=CommentReplyCreateRequest(
                resource_id=resource_id.strip(),
                reply_to=reply_to.strip(),
                content=content,
                image_urls=image_urls or [],
            ),
        )
        return {"comment_id": comment_id, "created": True}


__all__ = ["register_create_comment_reply_tool"]
