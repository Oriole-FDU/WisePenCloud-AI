from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import MovePersonalResourcesToTrashRequest
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient


def register_move_resources_to_trash_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="move_resources_to_trash",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="移入回收站", description="将资源移入回收站。"),
        failure_reason="Resource Trash Failed",
    ))
    @mcp.tool(
        name="move_resources_to_trash",
        description=(
            "Move one or more current-user-owned resources to the personal trash. This does not permanently delete the "
            "resource or its underlying file."
        ),
    )
    async def move_resources_to_trash(
        resource_ids: Annotated[list[str], Field(min_length=1, description="Resource ids to move to trash.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resolved_resource_ids = [str(resource_id).strip() for resource_id in resource_ids if str(resource_id).strip()]
        if not resolved_resource_ids:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "resource_ids must not be empty.")

        data = await resource_client.move_personal_resources_to_trash(
            request=MovePersonalResourcesToTrashRequest(resource_ids=resolved_resource_ids),
        )
        return {"resource_count": data.resource_count, "updated": True}


__all__ = ["register_move_resources_to_trash_tool"]
