from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import OutputCacheSpecMeta, OutputCacheStrategy, ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient

_SORT_BY_VALUES = frozenset({"CREATE_TIME", "LIKE_COUNT"})


def register_list_comments_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="list_comments",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="列出评论", description="分页列出资源的顶级评论。"),
        output_cache_spec=OutputCacheSpecMeta(strategy=OutputCacheStrategy.ALWAYS_KEEP_RAW),
        failure_reason="Comment List Failed",
    ))
    @mcp.tool(
        name="list_comments",
        description=(
            "List the top-level comments of a Wisepen resource the current user can view, paged. "
            "sort_by is CREATE_TIME or LIKE_COUNT. Use list_comment_replies to expand a comment's replies."
        ),
    )
    async def list_comments(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
        sort_by: Annotated[str, Field(description="Sort field: CREATE_TIME or LIKE_COUNT.")] = "CREATE_TIME",
        page: Annotated[int, Field(ge=1, description="Page number starting from 1.")] = 1,
        size: Annotated[int, Field(ge=1, le=50, description="Page size.")] = 10,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        sort_by = sort_by.strip().upper()
        if sort_by not in _SORT_BY_VALUES:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "sort_by must be CREATE_TIME or LIKE_COUNT.")

        page_data = await resource_client.list_comments(resource_id=resource_id, sort_by=sort_by, page=page, size=size)
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
        return {"resource_id": resource_id, "total": page_data.total, "items": items}


__all__ = ["register_list_comments_tool"]
