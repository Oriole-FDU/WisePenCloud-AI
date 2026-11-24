from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import InlineCommentItemCreateRequest
from wisepen_mcp.service_client import ResourceClient


def register_add_inline_comment_item_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="add_inline_comment_item",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="追加行内批注消息", description="向已有行内批注追加一条消息。"),
        failure_reason="Inline Comment Append Failed",
    ))
    @mcp.tool(
        name="add_inline_comment_item",
        description=(
            "Append a message to an existing inline annotation card. inline_comment_id is from list_inline_comments; "
            "the current user must have INLINE_COMMENT."
        ),
    )
    async def add_inline_comment_item(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
        inline_comment_id: Annotated[str, Field(min_length=1, description="Inline annotation id from list_inline_comments.")],
        content: Annotated[str, Field(min_length=1, description="Message text.")],
        content_version: Annotated[int | None, Field(description="Optional current content version.")] = None,
        image_urls: Annotated[list[str] | None, Field(description="Optional image URL list.")] = None,
        mention_user_ids: Annotated[list[str] | None, Field(description="Optional mentioned user id list.")] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        item_id = await resource_client.add_inline_comment_item(
            request=InlineCommentItemCreateRequest(
                resource_id=resource_id.strip(),
                inline_comment_id=inline_comment_id.strip(),
                content_version=content_version,
                content=content,
                image_urls=image_urls or [],
                mention_user_ids=mention_user_ids or [],
            ),
        )
        return {"inline_comment_id": inline_comment_id.strip(), "item_id": item_id, "created": True}


__all__ = ["register_add_inline_comment_item_tool"]
