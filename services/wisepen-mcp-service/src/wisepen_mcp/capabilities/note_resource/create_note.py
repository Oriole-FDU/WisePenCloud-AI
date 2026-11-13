from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import NoteClient


def register_create_note_tool(mcp: FastMCP, note_client: NoteClient) -> None:
    @mcp.tool(
        name="create_note",
        description=(
            "Create an empty Wisepen Note owned by the current user. This tool creates only the Note resource; it does "
            "not commit generated text as document content. To add AI suggestions, call read_current_note_for_edit with "
            "the returned resource_id, then call apply_current_note_edits with appendRootBlock operations and the "
            "returned yjs-v1 version. All generated blocks remain subject to user acceptance or rejection."
        ),
    )
    async def create_note(
        title: Annotated[str, Field(description="Display title for the new Note resource.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        title = title.strip()
        if not title:
            raise ServiceException(McpErrorCode.NOTE_AI_REQUEST_INVALID, "title must not be blank.")

        resource_id = await note_client.create_note(
            title=title,
            mount_target_tag_id=None,
        )
        return {
            "resource_id": resource_id,
            "resource_type": "NOTE",
            "title": title,
            "created": True,
        }


__all__ = ["register_create_note_tool"]
