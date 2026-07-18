from __future__ import annotations

from mcp.server.transport_security import TransportSecuritySettings
from mcp.server.fastmcp import FastMCP
from wisepen_mcp.service_client import AIAssetClient

from .create_skill_info import register_create_skill_info_tool
from .get_skill_info import register_get_skill_info_tool
from .update_skill_info import register_update_skill_info_tool
from .upload_skill_draft_asset import register_upload_skill_draft_asset_tool


def build_skill_creator_mcp(ai_asset_client: AIAssetClient) -> FastMCP:
    mcp = FastMCP(
        "wisepen-mcp-service",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    register_create_skill_info_tool(mcp, ai_asset_client)
    register_get_skill_info_tool(mcp, ai_asset_client)
    register_update_skill_info_tool(mcp, ai_asset_client)
    register_upload_skill_draft_asset_tool(mcp, ai_asset_client)

    return mcp
