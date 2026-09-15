from mcp.server.fastmcp import FastMCP

from wisepen_mcp.capabilities.rag.tools import register_rag_tools
from wisepen_mcp.service_client import RagServiceClient


def register_rag_tools_on_server(mcp: FastMCP, rag_client: RagServiceClient) -> None:
    register_rag_tools(mcp, rag_client)


__all__ = ["register_rag_tools_on_server"]
