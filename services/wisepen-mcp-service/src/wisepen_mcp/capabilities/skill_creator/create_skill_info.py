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


def register_create_skill_info_tool(mcp: FastMCP, ai_asset_client: AIAssetClient) -> None:
    @mcp.tool(
        name="create_skill_info",
        description=(
            "Create the Skill info record for a new Wisepen AIAsset Skill. "
            "Use this only when the user wants to save a new Skill draft and no resource_id exists yet. "
            "Returns resource_id and draft_version for later upload_skill_draft_asset calls. "
            "Do not use this for existing Skills; use get_skill_info or update_skill_info instead. "
            "This tool does not upload assets or publish."
        ),
    )
    async def create_skill_info(
        title: Annotated[str, Field(description="Resource display title for the new Wisepen Skill.")],
        name: Annotated[str, Field(description="Skill frontmatter name. Must be lowercase hyphen-case.")],
        description: Annotated[
            str,
            Field(description="Skill trigger description. Should match /SKILL.md frontmatter description."),
        ],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        title = title.strip()
        name = name.strip()
        description = description.strip()
        if not title:
            raise ServiceException(McpErrorCode.SKILL_INFO_INVALID, "title must not be blank.")
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

        resource_id = await ai_asset_client.create_skill_by_agent(title=title, name=name, description=description)
        return {
            "resource_id": resource_id,
            "draft_version": 1,
            "name": name,
            "created": True,
        }
