from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import SetPersonalResourcesPathTagRequest
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient


def register_move_resources_to_folder_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="move_resources_to_folder",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.MEDIUM, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="移动资源到文件夹", description="将资源移动到个人文件夹。"),
        failure_reason="Resource Move Failed",
    ))
    @mcp.tool(
        name="move_resources_to_folder",
        description=(
            "Move one or more current-user-owned resources to a target folder in the personal space. The target must be "
            "a personal folder (path tag) id from get_resource_tag_tree."
        ),
    )
    async def move_resources_to_folder(
        resource_ids: Annotated[list[str], Field(min_length=1, description="Resource ids to move.")],
        target_folder_tag_id: Annotated[
            str,
            Field(min_length=1, description="Target personal folder tag_id from get_resource_tag_tree."),
        ],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resolved_resource_ids = [str(resource_id).strip() for resource_id in resource_ids if str(resource_id).strip()]
        if not resolved_resource_ids:
            raise ServiceException(McpErrorCode.CLOUD_DISK_REQUEST_INVALID, "resource_ids must not be empty.")
        target_folder_tag_id = target_folder_tag_id.strip()

        data = await resource_client.set_personal_resources_path_tag(
            request=SetPersonalResourcesPathTagRequest(
                resource_ids=resolved_resource_ids,
                target_path_tag_id=target_folder_tag_id,
            ),
        )
        return {"resource_count": data.resource_count, "updated": True}


__all__ = ["register_move_resources_to_folder_tool"]
