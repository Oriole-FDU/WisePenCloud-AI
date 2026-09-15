from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from wisepen_mcp.service_client import RagServiceClient


def _clean_ids(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in values if item.strip()))


def register_reading_tools(mcp: FastMCP, client: RagServiceClient) -> None:
    @mcp.tool(name="rag_read_pages", description="Read up to 20 pages from one visible document resource.")
    async def rag_read_pages(
        resource_id: Annotated[str, Field(min_length=1)],
        page_labels: Annotated[list[str], Field(min_length=1, max_length=20)],
    ) -> dict[str, Any]:
        resource = resource_id.strip()
        labels = _clean_ids(page_labels)
        if not resource or not labels:
            raise ValueError("resource_id and page_labels must not be blank")
        return await client.read_pages(resource_id=resource, page_labels=labels)

    @mcp.tool(name="rag_read_sections", description="Read up to 20 visible sections with bounded expansion depth.")
    async def rag_read_sections(
        section_ids: Annotated[list[str], Field(min_length=1, max_length=20)],
        recursive: Annotated[bool, Field(description="Include recursively expanded child content.")] = False,
        max_depth: Annotated[int, Field(ge=0, le=5, description="Maximum recursive child depth.")] = 1,
    ) -> dict[str, Any]:
        sections = _clean_ids(section_ids)
        if not sections:
            raise ValueError("section_ids must not be blank")
        return await client.read_sections(
            section_ids=sections,
            recursive=recursive,
            max_depth=max_depth,
        )

    @mcp.tool(name="rag_get_neighborhood", description="Read the visible heading neighborhood for up to 20 sections.")
    async def rag_get_neighborhood(
        section_ids: Annotated[list[str], Field(min_length=1, max_length=20)],
        sibling_steps: Annotated[int, Field(ge=0, le=5, description="Sibling headings on each side.")] = 1,
    ) -> dict[str, Any]:
        sections = _clean_ids(section_ids)
        if not sections:
            raise ValueError("section_ids must not be blank")
        return await client.get_neighborhood(section_ids=sections, sibling_steps=sibling_steps)

    @mcp.tool(name="rag_get_global_outline", description="Read a bounded heading outline of one visible document.")
    async def rag_get_global_outline(
        resource_id: Annotated[str, Field(min_length=1)],
        max_level: Annotated[int, Field(ge=0, le=6, description="Maximum heading level; 0 means all levels.")] = 2,
    ) -> dict[str, Any]:
        resource = resource_id.strip()
        if not resource:
            raise ValueError("resource_id must not be blank")
        return await client.get_global_outline(resource_id=resource, max_level=max_level)
