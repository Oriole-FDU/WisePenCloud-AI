from __future__ import annotations

from typing import Annotated, Any

from common.mcp import OutputCacheSpecMeta, OutputCacheStrategy, ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.service_client import ResourceClient


def register_list_comment_replies_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="list_comment_replies",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="列出评论回复", description="分页列出某顶级评论下的回复。"),
        output_cache_spec=OutputCacheSpecMeta(strategy=OutputCacheStrategy.ALWAYS_KEEP_RAW),
        failure_reason="Comment Reply List Failed",
    ))
    @mcp.tool(
        name="list_comment_replies",
        description=(
            "List the replies under one top-level comment, paged (newest first). "
            "root_comment_id is a top-level comment id from list_comments."
        ),
    )
    async def list_comment_replies(
        root_comment_id: Annotated[str, Field(min_length=1, description="Top-level comment id from list_comments.")],
        page: Annotated[int, Field(ge=1, description="Page number starting from 1.")] = 1,
        size: Annotated[int, Field(ge=1, le=50, description="Page size.")] = 10,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        root_comment_id = root_comment_id.strip()
        page_data = await resource_client.list_comment_replies(root_comment_id=root_comment_id, page=page, size=size)
        items = [
            {
                "comment_id": item.comment_id,
                "resource_id": item.resource_id,
                "author_id": item.author_id,
                "content": item.content,
                "image_urls": item.image_urls,
                "like_count": item.like_count,
                "reply_count": item.reply_count,
                "comment_type": item.comment_type,
                "ai_authored": item.ai_authored,
                "author_info": item.author_info,
                "reply_to_user_info": item.reply_to_user_info,
                "create_time": item.create_time,
                "deleted": item.deleted,
            }
            for item in page_data.items
        ]
        return {"root_comment_id": root_comment_id, "total": page_data.total, "items": items}


__all__ = ["register_list_comment_replies_tool"]
