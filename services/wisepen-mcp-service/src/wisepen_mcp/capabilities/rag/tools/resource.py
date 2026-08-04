from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field, StringConstraints

from common.core.exceptions import ServiceException

from wisepen_mcp.capabilities.core.tools import CacheableText
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import RagServiceClient

from .common import append_cacheable_text, preview

_SNAPSHOT_DESCRIPTION = (
    "Description:\n"
    "Call this first when you need the resource's parsed locator map. It returns "
    "the current applied revision, total length, and all locator entries.\n\n"
    "Output:\n"
    "Use locators[] to choose a section/page/anchor name for the next read call. "
    "This tool does not return body content."
)

_READ_DESCRIPTION = (
    "Description:\n"
    "Read resource content by exact locator name or by Python-slice offset range. Use a locator "
    "when the structure already points at the right section/page/anchor. Use start "
    "and end when you only know the raw character span.\n\n"
    "Input:\n"
    "Provide locator_name OR start/end. Offsets follow Python slice semantics: "
    "start is inclusive, end is exclusive, negative offsets count from the end, "
    "and omitted offsets read from the beginning or to the end.\n\n"
    "Output:\n"
    "windows[] returns the selected text windows. Each window also gets a "
    "content_index so downstream tools can reuse the cached text. Use "
    "rag_get_resource_snapshot for the resource locator map."
)

_RESOURCE_ID = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    Field(description="The private resource_id returned by upstream document ingestion."),
]

_LOCATOR_NAME = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    Field(
        description=(
            "A locator name from rag_get_resource_snapshot, such as "
            "\"section:训练流程 > 参数配置\" or \"page:12\"."
        ),
    ),
]


def register_resource_tools(mcp: FastMCP, client: RagServiceClient) -> None:
    @mcp.tool(name="rag_get_resource_snapshot", description=_SNAPSHOT_DESCRIPTION)
    async def rag_get_resource_snapshot(resource_id: _RESOURCE_ID) -> dict[str, Any]:
        return _render_snapshot_result(
            await client.get_resource_snapshot(resource_id=resource_id)
        )

    @mcp.tool(name="rag_read_source", description=_READ_DESCRIPTION)
    async def rag_read_source(
        resource_id: _RESOURCE_ID,
        locator_name: _LOCATOR_NAME | None = None,
        start: Annotated[
            int | None,
            Field(description="Optional inclusive character start offset. Negative values count from the end."),
        ] = None,
        end: Annotated[
            int | None,
            Field(description="Optional exclusive character end offset. Negative values count from the end."),
        ] = None,
    ) -> dict[str, Any]:
        if locator_name is not None and (start is not None or end is not None):
            raise ServiceException(
                McpErrorCode.RAG_NAVIGATION_INVALID,
                "locator_name cannot be combined with start/end.",
            )
        return _render_read_result(
            await client.read_source(
                resource_id=resource_id,
                locator_name=locator_name,
                start=start,
                end=end,
            )
        )


def _render_snapshot_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "visible_result": {
            "resource_id": result["resource_id"],
            "document_version": result["document_version"],
            "content_revision": result["content_revision"],
            "total_length": result["total_length"],
            "locators": [
                {
                    "locator_index": locator["locator_index"],
                    "name": locator["name"],
                    "kind": locator["kind"],
                    "start_offset": locator["start_offset"],
                    "end_offset": locator["end_offset"],
                    "section_path": locator["section_path"],
                }
                for locator in result["locators"]
            ],
        },
        "cacheable_texts": [],
    }


def _render_read_result(result: dict[str, Any]) -> dict[str, Any]:
    cacheable_texts: list[CacheableText] = []
    windows = [
        _window_payload(result, window, cacheable_texts)
        for window in result["windows"]
    ]
    return {
        "visible_result": {
            "resource_id": result["resource_id"],
            "content_revision": result["content_revision"],
            "document_version": result["document_version"],
            "locator_name": result["locator_name"],
            "reason": result["reason"],
            "windows": windows,
        },
        "cacheable_texts": cacheable_texts,
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
        },
    )
    return {
        "content_index": content_index,
        "preview": preview(window["text"]),
        "start_offset": window["start_offset"],
        "end_offset": window["end_offset"],
        "source_spans": window["source_spans"],
        "metadata": window["metadata"],
    }
