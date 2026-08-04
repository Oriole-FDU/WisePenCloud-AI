from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field, StringConstraints

from wisepen_mcp.capabilities.core.tools import CacheableText
from wisepen_mcp.service_client import RagServiceClient

from .common import append_cacheable_text, preview

_STRUCTURE_DESCRIPTION = (
    "Description:\n"
    "Call this first when you need the resource's parsed document structure. It returns "
    "the current applied revision, page labels, and the full section tree without body text.\n\n"
    "Output:\n"
    "Use pages[].page_label with rag_get_page_content, or sections[].section_id with "
    "rag_get_section_content. This tool does not return body content."
)

_PAGE_CONTENT_DESCRIPTION = (
    "Description:\n"
    "Read one or more pages from a resource by page labels returned by "
    "rag_get_document_structure.\n\n"
    "Input:\n"
    "Provide page_labels as a list, such as [\"5\", \"6\", \"12\"]. Keep requests tight.\n\n"
    "Output:\n"
    "items[] is grouped by requested page label. Each window gets a content_index so "
    "downstream tools can reuse the cached text."
)

_SECTION_CONTENT_DESCRIPTION = (
    "Description:\n"
    "Read one or more sections from a resource by section_id values returned by "
    "rag_get_document_structure.\n\n"
    "Input:\n"
    "Provide section_ids as a list. A section read returns that section's own reading "
    "blocks, not descendant sections; pass child section_ids too when you need them.\n\n"
    "Output:\n"
    "items[] is grouped by requested section_id. Each window gets a content_index so "
    "downstream tools can reuse the cached text."
)

_RESOURCE_ID = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    Field(description="The private resource_id returned by upstream document ingestion."),
]

_PAGE_LABELS = Annotated[
    tuple[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)], ...],
    Field(
        min_length=1,
        max_length=20,
        description="Page labels from rag_get_document_structure, for example [\"5\", \"6\"].",
    ),
]

_SECTION_IDS = Annotated[
    tuple[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)], ...],
    Field(
        min_length=1,
        max_length=20,
        description="Section IDs from rag_get_document_structure.",
    ),
]


def register_resource_tools(mcp: FastMCP, client: RagServiceClient) -> None:
    @mcp.tool(name="rag_get_document_structure", description=_STRUCTURE_DESCRIPTION)
    async def rag_get_document_structure(resource_id: _RESOURCE_ID) -> dict[str, Any]:
        return _render_structure_result(
            await client.get_document_structure(resource_id=resource_id)
        )

    @mcp.tool(name="rag_get_page_content", description=_PAGE_CONTENT_DESCRIPTION)
    async def rag_get_page_content(
        resource_id: _RESOURCE_ID,
        page_labels: _PAGE_LABELS,
    ) -> dict[str, Any]:
        return _render_read_result(
            await client.get_page_content(
                resource_id=resource_id,
                page_labels=page_labels,
            )
        )

    @mcp.tool(name="rag_get_section_content", description=_SECTION_CONTENT_DESCRIPTION)
    async def rag_get_section_content(
        resource_id: _RESOURCE_ID,
        section_ids: _SECTION_IDS,
    ) -> dict[str, Any]:
        return _render_read_result(
            await client.get_section_content(
                resource_id=resource_id,
                section_ids=section_ids,
            )
        )


def _render_structure_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "visible_result": {
            "resource_id": result["resource_id"],
            "document_version": result["document_version"],
            "content_revision": result["content_revision"],
            "total_length": result["total_length"],
            "pages": [
                {
                    "page_label": page["page_label"],
                }
                for page in result["pages"]
            ],
            "sections": [_section_payload(section) for section in result["sections"]],
        },
        "cacheable_texts": [],
    }


def _render_read_result(result: dict[str, Any]) -> dict[str, Any]:
    cacheable_texts: list[CacheableText] = []
    items = [
        _item_payload(
            item,
            [
                _window_payload(result, window, cacheable_texts)
                for window in item["windows"]
            ],
        )
        for item in result["items"]
    ]
    return {
        "visible_result": {
            "resource_id": result["resource_id"],
            "content_revision": result["content_revision"],
            "document_version": result["document_version"],
            "items": items,
        },
        "cacheable_texts": cacheable_texts,
    }


def _section_payload(section: dict[str, Any]) -> dict[str, Any]:
    return {
        "section_id": section["section_id"],
        "title": section["title"],
        "level": section["level"],
        "section_path": section["section_path"],
        "has_content": section["has_content"],
        "children": [_section_payload(child) for child in section["children"]],
    }


def _item_payload(item: dict[str, Any], windows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "key": item["key"],
        "kind": item["kind"],
        "reason": item["reason"],
        "windows": windows,
    }


def _window_payload(
    result: dict[str, Any],
    window: dict[str, Any],
    cacheable_texts: list[CacheableText],
) -> dict[str, Any]:
    content_index = append_cacheable_text(
        cacheable_texts,
        window["text"],
        metadata={
            "resource_id": result["resource_id"],
            "content_revision": result["content_revision"],
            "document_version": result["document_version"],
            "start_offset": window["start_offset"],
            "end_offset": window["end_offset"],
            "source_spans": window["source_spans"],
            "locator_names": window["locator_names"],
            "page_labels": window["page_labels"],
            "section_paths": window["section_paths"],
            "anchor_labels": window["anchor_labels"],
        },
    )
    return {
        "content_index": content_index,
        "preview": preview(window["text"]),
        "start_offset": window["start_offset"],
        "end_offset": window["end_offset"],
        "source_spans": window["source_spans"],
        "locator_names": window["locator_names"],
        "page_labels": window["page_labels"],
        "section_paths": window["section_paths"],
        "anchor_labels": window["anchor_labels"],
        "metadata": window["metadata"],
    }
