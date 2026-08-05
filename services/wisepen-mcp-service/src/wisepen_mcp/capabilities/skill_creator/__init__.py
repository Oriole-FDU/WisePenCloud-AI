from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from wisepen_mcp.service_client import AIAssetClient

from .create_skill_info import register_create_skill_info_tool
from .get_skill_info import register_get_skill_info_tool
from .update_skill_info import register_update_skill_info_tool
from .upload_skill_draft_asset import register_upload_skill_draft_asset_tool


def register_skill_creator_tools(
    mcp: FastMCP,
    ai_asset_client: AIAssetClient,
) -> None:
    register_create_skill_info_tool(mcp, ai_asset_client)
    register_get_skill_info_tool(mcp, ai_asset_client)
    register_update_skill_info_tool(mcp, ai_asset_client)
    register_upload_skill_draft_asset_tool(mcp, ai_asset_client)


__all__ = ["register_skill_creator_tools"]
