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
            "Read the currently open Wisepen note as an XML editing snapshot. Use the current note resource_id "
            "from application_context.workspace_open_resource.resource_id. The returned version and public block ids "
            "must be reused when applying edits. For quick text-only loading of any visible note resource, use "
            "read_note_resource_text instead."
        ),
    )
    async def read_current_note_for_edit(
        resource_id: Annotated[str, Field(description="Current open note resource id from workspace_open_resource.")],
        scope: Annotated[dict[str, Any] | None, Field(description="Optional note read scope; omit unless narrowing to known blocks or ranges.")] = None,
        include_ai_content: Annotated[bool | None, Field(description="Whether to include existing AI-authored note content when relevant.")] = None,
        version: Annotated[str | None, Field(description="Version returned by a previous read of the same current note.")] = None,
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
