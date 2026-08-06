from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from wisepen_mcp.service_client import RagServiceClient

from .common import RagTextRenderBudget
from .navigation import register_navigation_tools
from .resource import register_resource_tools


def register_rag_tools(
    mcp: FastMCP,
    client: RagServiceClient,
    *,
    direct_text_window_char_budget: int = 24_000,
    direct_text_total_char_budget: int = 48_000,
) -> None:
    text_budget = RagTextRenderBudget(
        window_char_budget=direct_text_window_char_budget,
        total_char_budget=direct_text_total_char_budget,
    )
    register_navigation_tools(mcp, client, text_budget=text_budget)
    register_resource_tools(mcp, client, text_budget=text_budget)


__all__ = ["register_rag_tools"]
