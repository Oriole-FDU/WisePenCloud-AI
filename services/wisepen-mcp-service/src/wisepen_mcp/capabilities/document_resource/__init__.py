from mcp.server.fastmcp import FastMCP
from wisepen_mcp.capabilities.document_resource.get_document_source_file import register_get_document_source_file_tool
from wisepen_mcp.capabilities.document_resource.read_document_resource_text import register_read_document_resource_text_tool
from wisepen_mcp.service_client import DocumentClient, ResourceClient


def register_document_resource_tools(
    mcp: FastMCP,
    resource_client: ResourceClient,
    document_client: DocumentClient,
) -> None:
    register_read_document_resource_text_tool(mcp, resource_client, document_client)
    register_get_document_source_file_tool(mcp, document_client, resource_client)


__all__ = ["register_document_resource_tools"]
