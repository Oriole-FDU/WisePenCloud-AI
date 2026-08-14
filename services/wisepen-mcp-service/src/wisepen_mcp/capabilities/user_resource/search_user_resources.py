from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import ResourceClient


_ALLOWED_RESOURCE_TYPES = frozenset({"NOTE", "PDF", "DOC", "DOCX", "PPT", "PPTX", "XLS", "XLSX"})
_DEFAULT_RESOURCE_TYPES = ["NOTE", "PDF", "DOC", "DOCX", "PPT", "PPTX", "XLS", "XLSX"]


def register_search_user_resources_tool(mcp: FastMCP, resource_client: ResourceClient) -> None:
    @mcp.tool(
        name="search_user_resources",
        description=(
            "Search the current user's visible Wisepen Note and document resources. This tool excludes AIAsset "
            "resources such as Skill and Agent. Results include resource_id values that can be passed to "
            "read_note_resource_text for Note resources or read_document_resource_text for document resources."
        ),
    )
    async def search_user_resources(
        query: Annotated[str, Field(min_length=1, description="Search keyword or phrase.")],
        resource_types: Annotated[
            list[str] | None,
            Field(description="Optional resource type filter. Allowed: NOTE, PDF, DOC, DOCX, PPT, PPTX, XLS, XLSX."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=20, description="Maximum result count for the first page.")] = 10,
    ) -> dict[str, Any]:
        resolved_types = []
        for resource_type in resource_types or _DEFAULT_RESOURCE_TYPES:
            value = str(resource_type or "").strip().upper()
            if value in _ALLOWED_RESOURCE_TYPES and value not in resolved_types:
                resolved_types.append(value)
        if not resolved_types:
            raise ServiceException(McpErrorCode.RESOURCE_REQUEST_INVALID, "resource_types contains no supported resource type.")

        page = await resource_client.search_user_resources(
            keyword=query,
            resource_types=resolved_types,
            page=1,
            size=limit,
        )
        items = []
        for item in page.get("list") or []:
            if not isinstance(item, dict):
                continue
            resource_type = item.get("resourceType")
            if isinstance(resource_type, dict):
                resource_type = resource_type.get("value") or resource_type.get("name") or resource_type.get("extension")
            items.append({
                "resource_id": item.get("resourceId") or "",
                "resource_type": str(resource_type or "").strip().upper(),
                "resource_name": item.get("resourceName") or "",
                "highlight_content": item.get("highlightContent") or "",
                "update_time": item.get("updateTime"),
            })
        return {
            "query": query,
            "resource_types": resolved_types,
            "total": int(page.get("total") or 0),
            "items": items,
        }


__all__ = ["register_search_user_resources_tool"]
