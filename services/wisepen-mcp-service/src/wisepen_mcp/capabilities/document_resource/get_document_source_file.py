from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import DocumentClient, ResourceClient


def register_get_document_source_file_tool(
    mcp: FastMCP,
    document_client: DocumentClient,
    resource_client: ResourceClient,
) -> None:
    @mcp.tool(
        name="get_document_source_file",
        description=(
            "Get a temporary signed download URL for the original source file of a current-user-visible Wisepen document. "
            "The URL is short-lived and is intended for the sandbox to download the source file; it requires DOWNLOAD_ORIGINAL permission. "
            "Use read_document_resource_text to read the document text instead."
        ),
    )
    async def get_document_source_file(
        resource_id: Annotated[str, Field(min_length=1, description="Document resource id from list_resources or search_resources.")],
    ) -> dict[str, Any]:
        if not SecurityContextHolder.get_user_id():
            raise PermissionException(PermissionErrorCode.NOT_LOGIN)

        resource_id = resource_id.strip()
        if not resource_id:
            raise ServiceException(McpErrorCode.DOCUMENT_REQUEST_INVALID, "resource_id must not be blank.")

        download_url = await document_client.get_source_file_download_url(resource_id=resource_id)
        base_info = await resource_client.get_resource_base_info(resource_id=resource_id)
        return {
            "resource_id": resource_id,
            "download_url": download_url,
            "resource_name": base_info.resource_name,
            "resource_type": base_info.resource_type.name if base_info.resource_type else None,
            "size": base_info.size,
        }


__all__ = ["register_get_document_source_file_tool"]
