from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import NoteCollabClient


def register_read_current_note_for_edit_tool(mcp: FastMCP, note_collab_client: NoteCollabClient) -> None:
    @mcp.tool(
        name="read_current_note_for_edit",
        description=(
            "Read any visible Wisepen note as an XML editing snapshot, whether or not it is currently open. Obtain "
            "resource_id from application_context.workspace_open_resource, search_user_resources, create_note, or an "
            "explicit user selection. Reuse the returned version and block ids when applying suggestions. For quick "
            "text-only loading that does not support editing, use read_note_resource_text instead."
        ),
    )
    async def read_current_note_for_edit(
        resource_id: Annotated[str, Field(description="Visible Note resource id selected, searched, created, or currently open.")],
        scope: Annotated[dict[str, Any] | None, Field(description="Optional note read scope; omit unless narrowing to known blocks or ranges.")] = None,
        include_ai_content: Annotated[bool | None, Field(description="Whether to include existing AI-authored note content when relevant.")] = None,
        version: Annotated[str | None, Field(description="Version returned by a previous structured read of the same note.")] = None,
    ) -> dict[str, str]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)
        resource_id = resource_id.strip()
        if not resource_id:
            raise ServiceException(McpErrorCode.NOTE_AI_REQUEST_INVALID, "resource_id must not be blank.")
        request: dict[str, Any] = {}
        if scope is not None:
            request["scope"] = scope
        if include_ai_content is not None:
            request["includeAiContent"] = include_ai_content
        if version is not None:
            request["version"] = version
        xml = await note_collab_client.read_note_xml(resource_id, request)
        return {"resource_id": resource_id, "xml": xml}


__all__ = ["register_read_current_note_for_edit_tool"]
