from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from mcp.server.fastmcp import Context
from pydantic import Field, StringConstraints

from wisepen_mcp.capabilities.core.tools import CacheableText, get_tool_context_value
from wisepen_mcp.domain.error_codes import McpErrorCode

_SOURCE_PREVIEW_CHARS = 600

StateId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    Field(description="Reuse the exact state_id returned by locate or an earlier navigation call."),
]


def session_id(ctx: Context) -> str:
    value = get_tool_context_value(ctx, "session_id")
    if not isinstance(value, str) or not value.strip():
        raise ServiceException(
            McpErrorCode.RAG_NAVIGATION_INVALID,
            "session_id is missing from MCP tool context.",
        )
    return value.strip()


def section_view_payload(
    view: dict[str, Any],
    cacheable_texts: list[CacheableText],
    source_content_indices: dict[str, int] | None = None,
) -> dict[str, Any]:
    section_path = view["section_path"]
    reading_blocks = []
    for block in view["reading_blocks"]:
        content_index = append_cacheable_text(
            cacheable_texts,
            block["raw_text"],
            metadata={
                "resource_id": view["resource_id"],
                "section_id": view["section_id"],
                "reading_block_id": block["block_id"],
                "section_path": section_path,
                "page_labels": block["page_labels"],
                "anchor_labels": block["anchor_labels"],
            },
        )
        reading_blocks.append(
            {
                "reading_block_id": block["block_id"],
                "content_index": content_index,
                "preview": preview(block["raw_text"]),
                "page_labels": block["page_labels"],
                "anchor_labels": block["anchor_labels"],
            }
        )

    evidence = []
    for source in view["evidence"]:
        content_index = append_cacheable_text(
            cacheable_texts,
            source["content"],
            metadata={
                "resource_id": source["resource_id"],
                "section_id": source["section_id"],
                "source_ref_id": source["ref_id"],
                "section_path": source["section_path"],
                "page_labels": source["page_labels"],
                "anchor_labels": source["anchor_labels"],
            },
        )
        if source_content_indices is not None:
            source_content_indices[source["ref_id"]] = content_index
        evidence.append(
            {
                "source_ref_id": source["ref_id"],
                "content_index": content_index,
                "preview": preview(source["content"]),
                "page_labels": source["page_labels"],
                "anchor_labels": source["anchor_labels"],
            }
        )

    return {
        "resource_id": view["resource_id"],
        "section_id": view["section_id"],
        "title": view["title"],
        "section_path": section_path,
        "preview": view["preview"],
        "has_content": view["has_content"],
        "reading_blocks": reading_blocks,
        "evidence": evidence,
        "frontier": _frontier_payload(view["frontier"]),
    }


def append_cacheable_text(
    cacheable_texts: list[CacheableText],
    text: str,
    *,
    metadata: dict[str, Any],
) -> int:
    content_index = len(cacheable_texts)
    cacheable_texts.append({"text": text, "is_md": True, "metadata": metadata})
    return content_index


def preview(text: str) -> str:
    value = text.strip()
    if len(value) <= _SOURCE_PREVIEW_CHARS:
        return value
    return f"{value[:_SOURCE_PREVIEW_CHARS].rstrip()}..."


def _frontier_payload(frontier: dict[str, Any]) -> dict[str, Any]:
    return {
        "parent": _section_choice_payload(frontier["parent"]),
        "previous": _section_choice_payload(frontier["previous"]),
        "next": _section_choice_payload(frontier["next"]),
        "children": [
            _section_choice_payload(child) for child in frontier["children"]
        ],
    }


def _section_choice_payload(section: dict[str, Any] | None) -> dict[str, Any] | None:
    if section is None:
        return None
    return {
        "section_id": section["section_id"],
        "title": section["title"],
        "section_path": section["section_path"],
        "preview": section["preview"],
        "has_content": section["has_content"],
    }
