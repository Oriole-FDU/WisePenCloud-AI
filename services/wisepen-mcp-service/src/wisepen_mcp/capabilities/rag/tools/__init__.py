from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from wisepen_mcp.service_client import RagServiceClient

from .expand import register_expand_tool
from .locate import register_locate_tool
from .resource import register_resource_tools
from .sections import register_sections_tool


def register_rag_tools(mcp: FastMCP, client: RagServiceClient) -> None:
    register_locate_tool(mcp, client)
    register_expand_tool(mcp, client)
    register_sections_tool(mcp, client)
    register_resource_tools(mcp, client)


__all__ = ["register_rag_tools"]
