from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import ResourceFavoriteRequest
from wisepen_mcp.service_client import ResourceClient


def register_change_resource_favorite_status_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="change_resource_favorite_status",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="更改资源收藏状态", description="收藏或取消收藏资源，并设置其收藏夹。"),
        failure_reason="Favorite Status Change Failed",
    ))
    @mcp.tool(
        name="change_resource_favorite_status",
        description=(
            "Favorite (favorite=true) or unfavorite (favorite=false) a resource for the current user. "
            "When favoriting, collection_ids optionally sets the collections to add it to (empty uses the default collection)."
        ),
    )
    async def change_resource_favorite_status(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
        favorite: Annotated[bool, Field(description="true to favorite, false to unfavorite.")],
        collection_ids: Annotated[
            list[str] | None,
            Field(description="Optional collection ids (from list_favorite_collections) when favoriting."),
        ] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        await resource_client.change_resource_favorite_status(
            request=ResourceFavoriteRequest(
                resource_id=resource_id.strip(),
                favorite=favorite,
                collection_ids=collection_ids,
            ),
        )
        return {"resource_id": resource_id.strip(), "favorite": favorite, "updated": True}


__all__ = ["register_change_resource_favorite_status_tool"]
