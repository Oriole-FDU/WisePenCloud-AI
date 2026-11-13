from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import AIAssetClient


def register_get_agent_version_bundle_info_tool(mcp: FastMCP, ai_asset_client: AIAssetClient) -> None:
    @mcp.tool(
        name="get_agent_version_bundle_info",
        description=(
            "Read the version bundle of a Wisepen AIAsset Agent: its runtime spec (systemPrompt, model/tool/memory policies), version status, and asset file metadata. "
            "Leave version empty to read the current published version. Use the returned spec as the base when calling update_agent_spec."
        ),
    )
    async def get_agent_version_bundle_info(
        resource_id: Annotated[str, Field(description="Wisepen Agent resource id.")],
        version: Annotated[int | None, Field(description="Optional agent version; empty uses the current published version.")] = None,
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        if not resource_id:
            raise ServiceException(McpErrorCode.AGENT_SPEC_INVALID, "resource_id must not be blank.")

        data = await ai_asset_client.get_agent_version_bundle_info(resource_id, version)
        return {
            "resource_id": data.resource_id,
            "version": data.version,
            "status": data.status.name if data.status else None,
            "spec": data.spec,
            "assets": data.assets,
        }


__all__ = ["register_get_agent_version_bundle_info_tool"]
