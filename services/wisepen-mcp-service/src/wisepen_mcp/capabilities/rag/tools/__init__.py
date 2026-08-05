from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from wisepen_mcp.service_client import RagServiceClient

from .navigation import register_navigation_tools
from .resource import register_resource_tools


def register_rag_tools(mcp: FastMCP, client: RagServiceClient) -> None:
    register_navigation_tools(mcp, client)
    register_resource_tools(mcp, client)


__all__ = ["register_rag_tools"]
