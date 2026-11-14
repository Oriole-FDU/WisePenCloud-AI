from mcp.server.fastmcp import FastMCP
from wisepen_mcp.capabilities.agent_resource.create_agent_info import register_create_agent_info_tool
from wisepen_mcp.capabilities.agent_resource.get_agent_info import register_get_agent_info_tool
from wisepen_mcp.capabilities.agent_resource.get_agent_version_bundle_info import register_get_agent_version_bundle_info_tool
from wisepen_mcp.capabilities.agent_resource.update_agent_info import register_update_agent_info_tool
from wisepen_mcp.capabilities.agent_resource.update_agent_spec import register_update_agent_spec_tool
from wisepen_mcp.service_client import AIAssetClient


def register_agent_resource_tools(mcp: FastMCP, ai_asset_client: AIAssetClient) -> None:
    register_create_agent_info_tool(mcp, ai_asset_client)
    register_get_agent_info_tool(mcp, ai_asset_client)
    register_update_agent_info_tool(mcp, ai_asset_client)
    register_update_agent_spec_tool(mcp, ai_asset_client)
    register_get_agent_version_bundle_info_tool(mcp, ai_asset_client)


__all__ = ["register_agent_resource_tools"]
