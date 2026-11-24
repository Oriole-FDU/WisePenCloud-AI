from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import FavoriteCollectionInfoUpdateRequest
from wisepen_mcp.service_client import ResourceClient


def register_update_favorite_collection_info_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="update_favorite_collection_info",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="修改收藏夹信息", description="修改收藏夹的名称或描述。"),
        failure_reason="Favorite Collection Update Failed",
    ))
    @mcp.tool(
        name="update_favorite_collection_info",
        description=(
            "Update a favorite collection's name and description (full update; pass null description to clear it). "
            "collection_id is from list_favorite_collections."
        ),
    )
    async def update_favorite_collection_info(
        collection_id: Annotated[str, Field(min_length=1, description="Collection id from list_favorite_collections.")],
        collection_name: Annotated[str, Field(min_length=1, description="New collection name.")],
        description: Annotated[str | None, Field(description="Optional description; null clears it.")] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        await resource_client.update_favorite_collection_info(
            request=FavoriteCollectionInfoUpdateRequest(
                collection_id=collection_id.strip(),
                collection_name=collection_name,
                description=description,
            ),
        )
        return {"collection_id": collection_id.strip(), "updated": True}


__all__ = ["register_update_favorite_collection_info_tool"]
