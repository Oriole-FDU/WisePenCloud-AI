from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from wisepen_mcp.service_client import RagServiceClient


def register_graph_tool(mcp: FastMCP, client: RagServiceClient) -> None:
    @mcp.tool(
        name="rag_search_graph",
        description="Search the user's visible knowledge graph with bounded traversal controls.",
    )
    async def rag_search_graph(
        query: Annotated[str, Field(min_length=1, description="Question or graph search phrase.")],
        level: Annotated[Literal["low", "high", "hybrid"], Field(description="Graph retrieval level.")] = "hybrid",
        seed_node_ids: Annotated[list[str] | None, Field(max_length=20, description="Optional entity ids from a prior RAG hit.")] = None,
        resource_ids: Annotated[list[str] | None, Field(max_length=20, description="Optional visible resource ids to narrow the search.")] = None,
        node_categories: Annotated[list[str] | None, Field(max_length=20, description="Optional node category filters.")] = None,
        relation_types: Annotated[list[str] | None, Field(max_length=20, description="Optional relation type filters.")] = None,
        direction: Annotated[Literal["in", "out", "both"], Field(description="Graph edge traversal direction.")] = "both",
        max_depth: Annotated[int, Field(ge=0, le=2, description="Maximum graph traversal depth.")] = 1,
        vector_top_n: Annotated[int, Field(ge=1, le=50, description="Vector candidates per graph branch.")] = 20,
        candidate_limit: Annotated[int, Field(ge=1, le=100, description="Maximum candidates sent to ranking.")] = 60,
        top_k: Annotated[int, Field(ge=1, le=10, description="Maximum graph hits to return.")] = 5,
    ) -> dict[str, Any]:
        value = query.strip()
        if not value:
            raise ValueError("query must not be blank")
        return await client.search_graph(
            query=value,
            seed_node_ids=[item.strip() for item in (seed_node_ids or []) if item.strip()],
            resource_ids=[item.strip() for item in (resource_ids or []) if item.strip()] or None,
            level=level,
            node_categories=[item.strip() for item in (node_categories or []) if item.strip()],
            relation_types=[item.strip() for item in (relation_types or []) if item.strip()],
            direction=direction,
            max_depth=max_depth,
            vector_top_n=vector_top_n,
            candidate_limit=candidate_limit,
            top_k=top_k,
        )
