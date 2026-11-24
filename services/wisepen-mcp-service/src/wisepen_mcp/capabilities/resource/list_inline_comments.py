from __future__ import annotations

from typing import Annotated, Any

from common.mcp import OutputCacheSpecMeta, OutputCacheStrategy, ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.service_client import ResourceClient


def register_list_inline_comments_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="list_inline_comments",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="列出行内批注", description="一次性列出资源上的行内批注。"),
        output_cache_spec=OutputCacheSpecMeta(strategy=OutputCacheStrategy.ALWAYS_KEEP_RAW),
        failure_reason="Inline Comment List Failed",
    ))
    @mcp.tool(
        name="list_inline_comments",
        description=(
            "List a resource's inline annotation cards (with their messages) in one call. The current user must have VIEW. "
            "Optionally filter by content_version and/or resolved. resource_id is from list_resources or search_resources."
        ),
    )
    async def list_inline_comments(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
        content_version: Annotated[int | None, Field(description="Optional content version filter.")] = None,
        resolved: Annotated[bool | None, Field(description="Optional resolved-status filter.")] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        data = await resource_client.list_inline_comments(
            resource_id=resource_id,
            content_version=content_version,
            resolved=resolved,
        )
        items = [
            {
                "inline_comment_id": item.inline_comment_id,
                "resource_id": item.resource_id,
                "applicable_from_version": item.applicable_from_version,
                "applicable_to_version": item.applicable_to_version,
                "creator_id": item.creator_id,
                "anchor_ref": item.anchor_ref,
                "resolved": item.resolved,
                "resolved_by": item.resolved_by,
                "resolved_at": item.resolved_at,
                "creator_info": item.creator_info,
                "resolved_by_info": item.resolved_by_info,
                "create_time": item.create_time,
                "update_time": item.update_time,
                "items": item.items,
            }
            for item in data
        ]
        return {"resource_id": resource_id, "items": items}


__all__ = ["register_list_inline_comments_tool"]
