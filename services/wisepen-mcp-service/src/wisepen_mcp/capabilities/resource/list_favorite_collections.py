from __future__ import annotations

from typing import Any

from common.mcp import OutputCacheSpecMeta, OutputCacheStrategy, ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from wisepen_mcp.service_client import ResourceClient


def register_list_favorite_collections_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="list_favorite_collections",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="列出收藏夹", description="列出当前用户的所有收藏夹。"),
        output_cache_spec=OutputCacheSpecMeta(strategy=OutputCacheStrategy.ALWAYS_KEEP_RAW),
        failure_reason="Favorite Collection List Failed",
    ))
    @mcp.tool(
        name="list_favorite_collections",
        description="List the current user's favorite collections (the default collection is placed first).",
    )
    async def list_favorite_collections() -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        data = await resource_client.list_favorite_collections()
        items = [
            {
                "collection_id": item.collection_id,
                "collection_name": item.collection_name,
                "description": item.description,
                "is_default": item.is_default,
                "item_count": item.item_count,
                "create_time": item.create_time,
            }
            for item in data
        ]
        return {"items": items}


__all__ = ["register_list_favorite_collections_tool"]
