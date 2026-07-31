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
) -> dict[str, Any]:
    section_path = view["section_path"]
    return {
        "resource_id": view["resource_id"],
        "section_id": view["section_id"],
        "title": view["title"],
        "section_path": section_path,
        "summary": view["summary"],
        "has_content": view["has_content"],
        "reading_blocks": [
            {
                "content_index": append_cacheable_text(
                    cacheable_texts,
                    block["raw_text"],
                    metadata={
                        "reading_block_id": block["block_id"],
                        "section_path": section_path,
                        "page_labels": block["page_labels"],
                        "anchor_labels": block["anchor_labels"],
                    },
                ),
                "preview": preview(block["raw_text"]),
            }
            for block in view["reading_blocks"]
        ],
        "evidence": [
            {
                "content_index": append_cacheable_text(
                    cacheable_texts,
                    source["content"],
                    metadata={
                        "source_ref_id": source["ref_id"],
                        "section_path": source["section_path"],
                        "page_label": source.get("page_label"),
                        "anchor_labels": source["anchor_labels"],
                    },
                ),
                "preview": preview(source["content"]),
            }
            for source in view["evidence"]
        ],
        "frontier": view["frontier"],
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
