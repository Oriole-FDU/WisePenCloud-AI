from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field, StringConstraints

from wisepen_mcp.capabilities.core.tools import CacheableText
from wisepen_mcp.service_client import RagServiceClient

from .common import StateId, section_view_payload, session_id

_DESCRIPTION = (
    "Description:\n"
    "Read full text for section_id values already returned by locate, expand, or "
    "a frontier entry. Use this when a section preview is relevant but incomplete.\n\n"
    "Output:\n"
    "reading_blocks contain the section text via content_index. evidence contains "
    "the original hit snippets. frontier suggests adjacent or child sections to read next; "
    "frontier entries are navigation choices, not evidence."
)

_SECTION_IDS = Annotated[
    tuple[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)], ...],
    Field(
        min_length=1, max_length=12,
        description=(
            "Section IDs already returned in sources or frontier entries. Select the "
            "sections whose full reading blocks are needed."
        ),
    ),
]


def register_sections_tool(mcp: FastMCP, client: RagServiceClient) -> None:
    @mcp.tool(name="knowledge_navigate_sections", description=_DESCRIPTION)
    async def knowledge_navigate_sections(
        state_id: StateId,
        section_ids: _SECTION_IDS,
        ctx: Context,
    ) -> dict[str, Any]:
        return _render_sections_result(
            await client.read_sections(
                session_id=session_id(ctx),
                state_id=state_id,
                section_ids=section_ids,
            )
        )


def _render_sections_result(result: dict[str, Any]) -> dict[str, Any]:
    cacheable_texts: list[CacheableText] = []
    return {
        "visible_result": {
            "state_id": result["state_id"],
            "sections": [
                section_view_payload(section, cacheable_texts)
                for section in result["sections"]
            ],
        },
        "cacheable_texts": cacheable_texts,
    }
