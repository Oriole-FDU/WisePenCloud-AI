"""通用 RAG MCP 工具。"""

from mcp.server.fastmcp import FastMCP

from wisepen_mcp.capabilities.rag.tools.general.graph import register_graph_tool
from wisepen_mcp.capabilities.rag.tools.general.hybrid import register_hybrid_tool
from wisepen_mcp.capabilities.rag.tools.general.reading import register_reading_tools
from wisepen_mcp.service_client import RagServiceClient


def register_general_rag_tools(mcp: FastMCP, client: RagServiceClient) -> None:
    register_hybrid_tool(mcp, client)
    register_graph_tool(mcp, client)
    register_reading_tools(mcp, client)


__all__ = ["register_general_rag_tools"]
