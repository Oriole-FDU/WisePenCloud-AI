from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field, StringConstraints

from wisepen_mcp.capabilities.core.tools import CacheableText
from wisepen_mcp.service_client import RagServiceClient

from .common import section_view_payload, session_id

_DESCRIPTION = (
    "Description:\n"
    "Call first when the answer may be in the user's private WisePen documents. "
    "It returns grounded sections plus graph node anchors for follow-up navigation.\n\n"
    "Output:\n"
    "Use sources[].evidence and sources[].reading_blocks previews to decide what "
    "to read. Each content_index points to an exact cached text entry. Reuse "
    "state_id with knowledge_navigate_sections for section text, or with "
    "knowledge_navigate_expand for returned node_id values. Nodes are anchors, not evidence."
)

_QUERY = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    Field(
        description=(
            "The complete question or concept to answer from the user's private "
            "documents. Include the subject and constraints needed to judge relevance."
        ),
    ),
]


def register_locate_tool(mcp: FastMCP, client: RagServiceClient) -> None:
    @mcp.tool(name="knowledge_navigate_locate", description=_DESCRIPTION)
    async def knowledge_navigate_locate(
        query: _QUERY,
        ctx: Context,
        max_results: Annotated[
            int,
            Field(
                ge=1, le=20,
                description="Maximum number of relevant private-document results to return.",
            ),
        ] = 10,
    ) -> dict[str, Any]:
        return _render_locate_result(
            await client.locate(
                session_id=session_id(ctx),
                query=query,
                max_results=max_results,
            )
        )


def _render_locate_result(result: dict[str, Any]) -> dict[str, Any]:
    cacheable_texts: list[CacheableText] = []
    return {
        "visible_result": {
            "state_id": result["state_id"],
            "nodes": result["nodes"],
            "sources": [
                section_view_payload(source, cacheable_texts)
                for source in result["sources"]
            ],
        },
        "cacheable_texts": cacheable_texts,
    }
