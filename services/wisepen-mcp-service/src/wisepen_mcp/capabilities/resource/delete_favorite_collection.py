from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import FavoriteCollectionDeleteRequest
from wisepen_mcp.service_client import ResourceClient


def register_delete_favorite_collection_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="delete_favorite_collection",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.HIGH, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="删除收藏夹", description="删除当前用户的一个收藏夹。"),
        failure_reason="Favorite Collection Delete Failed",
    ))
    @mcp.tool(
        name="delete_favorite_collection",
        description=(
            "Delete a favorite collection. keep_resources_to_default=true moves resources that only belong to this "
            "collection into the default collection; false removes those favorite references. The default collection cannot be deleted."
        ),
    )
    async def delete_favorite_collection(
        collection_id: Annotated[str, Field(min_length=1, description="Collection id from list_favorite_collections.")],
        keep_resources_to_default: Annotated[
            bool,
            Field(description="true to move orphaned favorites to the default collection, false to remove them."),
        ] = False,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        await resource_client.delete_favorite_collection(
            request=FavoriteCollectionDeleteRequest(
                collection_id=collection_id.strip(),
                keep_resources_to_default=keep_resources_to_default,
            ),
        )
        return {"collection_id": collection_id.strip(), "deleted": True}


__all__ = ["register_delete_favorite_collection_tool"]
