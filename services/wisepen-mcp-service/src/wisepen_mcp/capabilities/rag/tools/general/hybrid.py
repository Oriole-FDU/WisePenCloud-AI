from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from wisepen_mcp.service_client import RagServiceClient


def register_hybrid_tool(mcp: FastMCP, client: RagServiceClient) -> None:
    @mcp.tool(
        name="rag_search_hybrid",
        description="Search the user's visible documents with server-controlled hybrid semantic retrieval.",
    )
    async def rag_search_hybrid(
        query: Annotated[str, Field(min_length=1, description="Question or search phrase.")],
        top_k: Annotated[int, Field(ge=1, le=10, description="Maximum parent contexts to return.")] = 5,
    ) -> dict[str, Any]:
        value = query.strip()
        if not value:
            raise ValueError("query must not be blank")
        return await client.search_hybrid(query=value, top_k=top_k)
