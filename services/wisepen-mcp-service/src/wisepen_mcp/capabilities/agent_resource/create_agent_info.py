from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import AIAssetClient


def register_create_agent_info_tool(mcp: FastMCP, ai_asset_client: AIAssetClient) -> None:
    @mcp.tool(
        name="create_agent_info",
        description=(
            "Create the Agent info record for a new Wisepen AIAsset Agent. "
            "Use this only when the user wants to save a new Agent draft and no resource_id exists yet. "
            "Returns resource_id and draft_version for later update_agent_spec calls. "
            "Do not use this for existing Agents; use get_agent_info or update_agent_info instead. "
            "This tool does not upload assets, set the runtime spec, or publish."
        ),
    )
    async def create_agent_info(
        title: Annotated[str, Field(description="Resource display title for the new Wisepen Agent.")],
        name: Annotated[str, Field(description="Agent name.")],
        description: Annotated[str, Field(description="Agent description.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        title = title.strip()
        name = name.strip()
        description = description.strip()
        if not title:
            raise ServiceException(McpErrorCode.AGENT_INFO_INVALID, "title must not be blank.")
        if not name:
            raise ServiceException(McpErrorCode.AGENT_INFO_INVALID, "name must not be blank.")
        if not description:
            raise ServiceException(McpErrorCode.AGENT_INFO_INVALID, "description must not be blank.")

        resource_id = await ai_asset_client.create_agent_by_agent(title=title, name=name, description=description)
        return {
            "resource_id": resource_id,
            "draft_version": 1,
            "name": name,
            "created": True,
        }


__all__ = ["register_create_agent_info_tool"]
