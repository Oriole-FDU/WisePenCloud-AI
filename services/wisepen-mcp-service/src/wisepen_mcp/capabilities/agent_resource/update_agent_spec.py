from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import AgentSpecUpdateRequest
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import AIAssetClient


def register_update_agent_spec_tool(mcp: FastMCP, ai_asset_client: AIAssetClient) -> None:
    @mcp.tool(
        name="update_agent_spec",
        description=(
            "Replace the full runtime spec of a Wisepen AIAsset Agent draft version. "
            "spec is the complete agent runtime configuration and replaces the existing one, so fetch the current spec first via get_agent_version_bundle_info and send the modified full object. "
            "Typical spec shape: systemPrompt plus optional modelPolicy, toolAndSkillPolicy, and memoryPolicy objects. "
            "Use the draft_version returned by create_agent_info or get_agent_info. This tool does not upload assets or publish."
        ),
    )
    async def update_agent_spec(
        resource_id: Annotated[str, Field(description="Wisepen Agent resource id.")],
        draft_version: Annotated[int, Field(description="Draft version returned by create_agent_info or get_agent_info.")],
        spec: Annotated[dict[str, Any], Field(description="Complete agent runtime spec object to replace the draft spec with.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        if not resource_id:
            raise ServiceException(McpErrorCode.AGENT_SPEC_INVALID, "resource_id must not be blank.")
        if not isinstance(draft_version, int) or isinstance(draft_version, bool) or draft_version <= 0:
            raise ServiceException(McpErrorCode.AGENT_SPEC_INVALID, "draft_version must be a positive integer.")
        if not isinstance(spec, dict) or not spec:
            raise ServiceException(McpErrorCode.AGENT_SPEC_INVALID, "spec must be a non-empty object.")

        await ai_asset_client.update_agent_spec(
            request=AgentSpecUpdateRequest(resource_id=resource_id, draft_version=draft_version, spec=spec),
        )
        return {
            "resource_id": resource_id,
            "draft_version": draft_version,
            "updated": True,
        }


__all__ = ["register_update_agent_spec_tool"]
