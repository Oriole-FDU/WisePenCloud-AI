from __future__ import annotations

import re
from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import AIAssetClient
from pydantic import Field


_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def register_update_skill_info_tool(mcp: FastMCP, ai_asset_client: AIAssetClient) -> None:
    @mcp.tool(
        name="update_skill_info",
        description=(
            "Update the name and description fields of an existing Wisepen AIAsset Skill info record. "
            "Use this only when the existing Skill metadata should change. "
            "Do not use this to read Skill info; use get_skill_info instead. "
            "This tool does not upload assets or publish."
        ),
    )
    async def update_skill_info(
        resource_id: Annotated[str, Field(description="Existing Wisepen Skill resource id.")],
        name: Annotated[str, Field(description="Skill frontmatter name. Must be lowercase hyphen-case.")],
        description: Annotated[
            str,
            Field(description="Skill trigger description. Should match /SKILL.md frontmatter description."),
        ],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        name = name.strip()
        description = description.strip()
        if not resource_id:
            raise ServiceException(McpErrorCode.SKILL_INFO_INVALID, "resource_id must not be blank.")
        if not name:
            raise ServiceException(McpErrorCode.SKILL_INFO_INVALID, "name must not be blank.")
        if not description:
            raise ServiceException(McpErrorCode.SKILL_INFO_INVALID, "description must not be blank.")
        if not _NAME_RE.fullmatch(name):
            raise ServiceException(
                McpErrorCode.SKILL_INFO_INVALID,
                "name must be lowercase hyphen-case using letters, digits, and single hyphens; "
                "it must not start or end with a hyphen.",
            )

        await ai_asset_client.update_skill_info(resource_id, name, description)
        return {
            "resource_id": resource_id,
            "name": name,
            "description": description,
            "updated": True,
        }
