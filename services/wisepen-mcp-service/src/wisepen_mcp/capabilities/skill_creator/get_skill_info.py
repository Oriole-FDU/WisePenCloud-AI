from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import AIAssetClient
from pydantic import Field


def register_get_skill_info_tool(mcp: FastMCP, ai_asset_client: AIAssetClient) -> None:
    @mcp.tool(
        name="get_skill_info",
        description=(
            "Read the Skill info record for an existing Wisepen AIAsset Skill by resource_id. "
            "Use this when updating an existing Skill or when you need its current name, description, source_type, version, and next draft_version. "
            "This tool does not create, update, upload assets, or publish."
        ),
    )
    async def get_skill_info(
        resource_id: Annotated[str, Field(description="Existing Wisepen Skill resource id.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        if not resource_id:
            raise ServiceException(McpErrorCode.SKILL_INFO_INVALID, "resource_id must not be blank.")

        skill_info = await ai_asset_client.get_skill_info(resource_id)
        if not skill_info.resource_id:
            raise ServiceException(
                McpErrorCode.SKILL_NOT_FOUND,
                f"Skill '{resource_id}' was not returned by AIAsset.",
            )

        return {
            "resource_id": skill_info.resource_id,
            "name": skill_info.name,
            "description": skill_info.description,
            "source_type": skill_info.source_type,
            "version": skill_info.version,
            "draft_version": skill_info.version + 1,
        }
