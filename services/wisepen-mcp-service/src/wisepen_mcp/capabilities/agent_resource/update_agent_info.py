from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import AIAssetClient


def register_update_agent_info_tool(mcp: FastMCP, ai_asset_client: AIAssetClient) -> None:
    @mcp.tool(
        name="update_agent_info",
        description=(
            "Update the name and description fields of an existing Wisepen AIAsset Agent info record. "
            "Use this only when the existing Agent metadata should change. "
            "Do not use this to read Agent info; use get_agent_info instead. "
            "This tool does not set the runtime spec or publish."
        ),
    )
    async def update_agent_info(
        resource_id: Annotated[str, Field(description="Existing Wisepen Agent resource id.")],
        name: Annotated[str, Field(description="Agent name.")],
        description: Annotated[str, Field(description="Agent description.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        name = name.strip()
        description = description.strip()
        if not resource_id:
            raise ServiceException(McpErrorCode.AGENT_INFO_INVALID, "resource_id must not be blank.")
        if not name:
            raise ServiceException(McpErrorCode.AGENT_INFO_INVALID, "name must not be blank.")
        if not description:
            raise ServiceException(McpErrorCode.AGENT_INFO_INVALID, "description must not be blank.")

        await ai_asset_client.update_agent_info(resource_id, name, description)
        return {
            "resource_id": resource_id,
            "name": name,
            "description": description,
            "updated": True,
        }


__all__ = ["register_update_agent_info_tool"]
