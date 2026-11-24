from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import FavoriteCollectionCreateRequest
from wisepen_mcp.service_client import ResourceClient


def register_create_favorite_collection_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="create_favorite_collection",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="新建收藏夹", description="为当前用户创建一个收藏夹。"),
        failure_reason="Favorite Collection Create Failed",
    ))
    @mcp.tool(
        name="create_favorite_collection",
        description="Create a new favorite collection for the current user.",
    )
    async def create_favorite_collection(
        collection_name: Annotated[str, Field(min_length=1, description="Collection name.")],
        description: Annotated[str | None, Field(description="Optional description.")] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        collection_id = await resource_client.create_favorite_collection(
            request=FavoriteCollectionCreateRequest(collection_name=collection_name, description=description),
        )
        return {"collection_id": collection_id, "created": True}


__all__ = ["register_create_favorite_collection_tool"]
