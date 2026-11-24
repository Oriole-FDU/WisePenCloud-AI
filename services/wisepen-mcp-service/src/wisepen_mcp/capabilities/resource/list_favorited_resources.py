from __future__ import annotations

from typing import Annotated, Any

from common.mcp import OutputCacheSpecMeta, OutputCacheStrategy, ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.service_client import ResourceClient


def register_list_favorited_resources_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="list_favorited_resources",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="列出收藏的资源", description="分页列出当前用户已收藏的资源。"),
        output_cache_spec=OutputCacheSpecMeta(strategy=OutputCacheStrategy.ALWAYS_KEEP_RAW),
        failure_reason="Favorited Resource List Failed",
    ))
    @mcp.tool(
        name="list_favorited_resources",
        description=(
            "List the current user's favorited resources, paged. Optionally filter by collection_id (from "
            "list_favorite_collections). Items the user can no longer discover are returned with accessible=false."
        ),
    )
    async def list_favorited_resources(
        collection_id: Annotated[
            str | None,
            Field(description="Optional collection id filter from list_favorite_collections."),
        ] = None,
        page: Annotated[int, Field(ge=1, description="Page number starting from 1.")] = 1,
        size: Annotated[int, Field(ge=1, le=100, description="Page size.")] = 20,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resolved_collection_id = collection_id.strip() if collection_id and collection_id.strip() else None
        page_data = await resource_client.list_favorited_resources(
            collection_id=resolved_collection_id,
            page=page,
            size=size,
        )
        items = [
            {
                "resource_id": item.resource_id,
                "resource_info": item.resource_info,
                "favorited_at": item.favorited_at,
                "collection_ids": item.collection_ids,
                "accessible": item.accessible,
            }
            for item in page_data.items
        ]
        return {"collection_id": resolved_collection_id, "total": page_data.total, "items": items}


__all__ = ["register_list_favorited_resources_tool"]
