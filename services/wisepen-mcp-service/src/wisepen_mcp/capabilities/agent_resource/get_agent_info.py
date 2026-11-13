from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import AIAssetClient


def register_get_agent_info_tool(mcp: FastMCP, ai_asset_client: AIAssetClient) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="get_agent_info",
        policy=ToolPolicyMeta(expose_by_default=True, risk_level=ToolRiskLevel.LOW, timeout_seconds=15.0, persist_output=True),
        ui_spec=ToolUISpecMeta(display_name="读取 Agent 信息", description="读取已有 Agent 的信息。"),
        failure_reason="Agent Info Load Failed",
    ))
    @mcp.tool(
        name="get_agent_info",
        description=(
            "Read the Agent info record for an existing Wisepen AIAsset Agent by resource_id. "
            "Use this when updating an existing Agent or when you need its current name, description, source_type, version, and next draft_version. "
            "This tool does not create, update, set the runtime spec, or publish."
        ),
    )
    async def get_agent_info(
        resource_id: Annotated[str, Field(description="Existing Wisepen Agent resource id.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        if not resource_id:
            raise ServiceException(McpErrorCode.AGENT_INFO_INVALID, "resource_id must not be blank.")

        agent_info = await ai_asset_client.get_agent_info(resource_id)
        if not agent_info.resource_id:
            raise ServiceException(
                McpErrorCode.AGENT_NOT_FOUND,
                f"Agent '{resource_id}' was not returned by AIAsset.",
            )

        return {
            "resource_id": agent_info.resource_id,
            "name": agent_info.name,
            "description": agent_info.description,
            "source_type": agent_info.source_type,
            "version": agent_info.version,
            "draft_version": agent_info.version + 1,
        }


__all__ = ["register_get_agent_info_tool"]
