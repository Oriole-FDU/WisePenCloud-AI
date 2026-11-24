from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import InlineCommentItemUpdateRequest
from wisepen_mcp.service_client import ResourceClient


def register_update_inline_comment_item_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="update_inline_comment_item",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="修改行内批注消息", description="修改行内批注卡片中的一条消息。"),
        failure_reason="Inline Comment Update Failed",
    ))
    @mcp.tool(
        name="update_inline_comment_item",
        description=(
            "Update one message inside an inline annotation card. Only the message author can edit it; the current user "
            "must have INLINE_COMMENT. item_id is from list_inline_comments."
        ),
    )
    async def update_inline_comment_item(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
        inline_comment_id: Annotated[str, Field(min_length=1, description="Inline annotation id from list_inline_comments.")],
        item_id: Annotated[str, Field(min_length=1, description="Message id from list_inline_comments.")],
        content: Annotated[str, Field(min_length=1, description="New message text.")],
        content_version: Annotated[int | None, Field(description="Optional current content version.")] = None,
        image_urls: Annotated[list[str] | None, Field(description="Optional image URL list.")] = None,
        mention_user_ids: Annotated[list[str] | None, Field(description="Optional mentioned user id list.")] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        await resource_client.update_inline_comment_item(
            request=InlineCommentItemUpdateRequest(
                resource_id=resource_id.strip(),
                inline_comment_id=inline_comment_id.strip(),
                item_id=item_id.strip(),
                content_version=content_version,
                content=content,
                image_urls=image_urls or [],
                mention_user_ids=mention_user_ids or [],
            ),
        )
        return {"inline_comment_id": inline_comment_id.strip(), "item_id": item_id.strip(), "updated": True}


__all__ = ["register_update_inline_comment_item_tool"]
