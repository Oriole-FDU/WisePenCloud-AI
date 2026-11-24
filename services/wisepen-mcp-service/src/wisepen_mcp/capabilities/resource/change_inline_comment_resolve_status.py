from __future__ import annotations

from typing import Annotated, Any

from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import InlineCommentResolveRequest
from wisepen_mcp.service_client import ResourceClient


def register_change_inline_comment_resolve_status_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="change_inline_comment_resolve_status",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="变更行内批注解决状态", description="将行内批注标记为已解决或恢复未解决。"),
        failure_reason="Inline Comment Resolve Failed",
    ))
    @mcp.tool(
        name="change_inline_comment_resolve_status",
        description=(
            "Mark an inline annotation card as resolved (true) or unresolved (false). The current user must be the card "
            "creator, resource owner, admin, or have EDIT. inline_comment_id is from list_inline_comments."
        ),
    )
    async def change_inline_comment_resolve_status(
        resource_id: Annotated[str, Field(min_length=1, description="Resource id from list_resources or search_resources.")],
        inline_comment_id: Annotated[str, Field(min_length=1, description="Inline annotation id from list_inline_comments.")],
        resolved: Annotated[bool, Field(description="Target resolved status: true to resolve, false to reopen.")],
        content_version: Annotated[int | None, Field(description="Optional current content version.")] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        await resource_client.change_inline_comment_resolve_status(
            request=InlineCommentResolveRequest(
                resource_id=resource_id.strip(),
                inline_comment_id=inline_comment_id.strip(),
                resolved=resolved,
                content_version=content_version,
            ),
        )
        return {"inline_comment_id": inline_comment_id.strip(), "resolved": resolved, "updated": True}


__all__ = ["register_change_inline_comment_resolve_status_tool"]
