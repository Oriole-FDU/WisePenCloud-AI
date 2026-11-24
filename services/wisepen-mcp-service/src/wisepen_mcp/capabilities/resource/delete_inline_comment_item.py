from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import InlineCommentItemDeleteRequest
from wisepen_mcp.service_client import ResourceClient


def register_delete_inline_comment_item_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="delete_inline_comment_item",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.HIGH, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="删除行内批注消息", description="删除行内批注卡片中的一条消息。"),
        failure_reason="Inline Comment Delete Failed",
    ))
    @mcp.tool(
        name="delete_inline_comment_item",
        description=(
            "Delete one message inside an inline annotation card; deleting the last message deletes the whole card. "
            "The current user must be the message author, resource owner, or admin. item_id is from list_inline_comments."
        ),
    )
    async def delete_inline_comment_item(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
        inline_comment_id: Annotated[str, Field(min_length=1, description="Inline annotation id from list_inline_comments.")],
        item_id: Annotated[str, Field(min_length=1, description="Message id from list_inline_comments.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        await resource_client.delete_inline_comment_item(
            request=InlineCommentItemDeleteRequest(
                resource_id=resource_id.strip(),
                inline_comment_id=inline_comment_id.strip(),
                item_id=item_id.strip(),
            ),
        )
        return {"inline_comment_id": inline_comment_id.strip(), "item_id": item_id.strip(), "deleted": True}


__all__ = ["register_delete_inline_comment_item_tool"]
