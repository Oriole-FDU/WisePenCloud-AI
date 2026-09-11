from mcp.server.fastmcp import FastMCP

from wisepen_mcp.capabilities.rag.tools.general import register_general_rag_tools
from wisepen_mcp.service_client import RagServiceClient


def register_rag_tools(mcp: FastMCP, rag_client: RagServiceClient) -> None:
    register_general_rag_tools(mcp, rag_client)


__all__ = ["register_rag_tools"]
