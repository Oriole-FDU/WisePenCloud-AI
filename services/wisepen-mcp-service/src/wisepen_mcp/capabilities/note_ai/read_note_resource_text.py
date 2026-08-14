from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import NoteClient, ResourceClient


def register_read_note_resource_text_tool(
    mcp: FastMCP,
    resource_client: ResourceClient,
    note_client: NoteClient,
) -> None:
    @mcp.tool(
        name="read_note_resource_text",
        description=(
            "Read the latest stored plain text for one current-user-visible Wisepen Note resource. This is only for "
            "fast loading, understanding, search follow-up, or summarization. It does not return XML, version, or "
            "public block ids, so it must not be used to edit a note. To edit the currently open note, use "
            "read_current_note_for_edit first and then apply_current_note_edits."
        ),
    )
    async def read_note_resource_text(
        resource_id: Annotated[str, Field(min_length=1, description="Note resource id returned by search_user_resources or selected by the user.")],
    ) -> dict[str, Any]:
        user_id = SecurityContextHolder.get_user_id()
        if not user_id:
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        group_roles: dict[str, int] = {}
        for group_id, role in SecurityContextHolder.get_group_role_map().items():
            code = getattr(role, "code", role)
            try:
                group_roles[str(group_id)] = int(code)
            except (TypeError, ValueError):
                continue

        resource_id = resource_id.strip()
        if not resource_id:
            raise ServiceException(McpErrorCode.RESOURCE_REQUEST_INVALID, "resource_id must not be blank.")
        resource_info = await resource_client.get_resource_info(
            resource_id=resource_id,
            user_id=str(user_id),
            group_roles=group_roles,
        )
        resource_type = resource_info.get("resourceType")
        if isinstance(resource_type, dict):
            resource_type = resource_type.get("value") or resource_type.get("name") or resource_type.get("extension")
        resource_type = str(resource_type or "").strip().upper()
        if resource_type != "NOTE":
            raise ServiceException(McpErrorCode.RESOURCE_REQUEST_INVALID, f"unsupported note resource type: {resource_type}")

        text_payload = await note_client.get_search_text(resource_id)
        return {
            "resource_id": resource_id,
            "resource_type": resource_type,
            "resource_name": resource_info.get("resourceName") or "",
            "content_type": "text/plain",
            "text": str(text_payload.get("searchText") or ""),
        }


__all__ = ["register_read_note_resource_text_tool"]
