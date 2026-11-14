from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import OutputCacheSpecMeta, OutputCacheStrategy, ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import ResourceType
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import NoteClient, ResourceClient


def register_read_note_resource_text_tool(
    mcp: FastMCP,
    resource_client: ResourceClient,
    note_client: NoteClient,
) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="read_note_resource_text",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=20.0,
            persist_output=True,
        ),
        ui_spec=ToolUISpecMeta(display_name="快速读取笔记文本", description="读取当前用户可见（无需已经打开）笔记的纯文本内容，用于快速了解，不能用于编辑。"),
        output_cache_spec=OutputCacheSpecMeta(strategy=OutputCacheStrategy.LONG_TEXT, cache_paths=("text",)),
        failure_reason="Note Resource Text Read Failed",
    ))
    @mcp.tool(
        name="read_note_resource_text",
        description=(
            "Read the latest stored plain text for one current-user-visible Wisepen Note resource. This is only for "
            "fast loading, understanding, search follow-up, or summarization. It does not return XML, version, or "
            "public block ids, so it cannot support block-level citations and must not be used to edit a note. "
            "For block structure, citations, or editing of any visible Note, use read_current_note_for_edit. To edit, "
            "read the structured snapshot first and then call apply_current_note_edits."
        ),
    )
    async def read_note_resource_text(
        resource_id: Annotated[str, Field(min_length=1, description="Note resource id returned by search_resources or selected by the user.")],
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
        if resource_info.resource_type is not ResourceType.NOTE:
            raise ServiceException(McpErrorCode.RESOURCE_REQUEST_INVALID, f"unsupported note resource type: {resource_info.resource_type}")

        text_payload = await note_client.get_search_text(resource_id)
        return {
            "resource_id": resource_id,
            "resource_type": resource_info.resource_type.name,
            "resource_name": resource_info.resource_name,
            "content_type": "text/plain",
            "text": text_payload.search_text,
        }


__all__ = ["register_read_note_resource_text_tool"]
