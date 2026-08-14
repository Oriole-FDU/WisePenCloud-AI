from mcp.server.fastmcp import FastMCP
from wisepen_mcp.capabilities.user_resource.read_document_resource_text import register_read_document_resource_text_tool
from wisepen_mcp.capabilities.user_resource.search_user_resources import register_search_user_resources_tool
from wisepen_mcp.service_client import DocumentClient, ResourceClient


def register_user_resource_tools(
    mcp: FastMCP,
    resource_client: ResourceClient,
    document_client: DocumentClient,
) -> None:
    register_search_user_resources_tool(mcp, resource_client)
    register_read_document_resource_text_tool(mcp, resource_client, document_client)

__all__ = ["register_user_resource_tools"]
