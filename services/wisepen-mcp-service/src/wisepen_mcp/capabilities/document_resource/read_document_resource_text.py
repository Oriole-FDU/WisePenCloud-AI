from __future__ import annotations

from typing import Annotated, Any

from common.core.exceptions import ServiceException
from common.mcp import OutputCacheSpecMeta, OutputCacheStrategy, ToolMeta, ToolPolicyMeta, ToolRiskLevel, ToolUISpecMeta, wisepen_mcp_meta
from common.security import PermissionErrorCode, PermissionException, SecurityContextHolder
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from wisepen_mcp.domain.entities import ResourceType
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import DocumentClient, ResourceClient


_DOCUMENT_RESOURCE_TYPES = frozenset({
    ResourceType.PDF, ResourceType.DOC, ResourceType.DOCX, ResourceType.PPT, ResourceType.PPTX, ResourceType.XLS, ResourceType.XLSX,
})


def register_read_document_resource_text_tool(
    mcp: FastMCP,
    resource_client: ResourceClient,
    document_client: DocumentClient,
) -> None:
    @wisepen_mcp_meta(ToolMeta(
        tool_name="read_document_resource_text",
        policy=ToolPolicyMeta(
            expose_by_default=True,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=20.0,
            persist_output=True,
        ),
        ui_spec=ToolUISpecMeta(display_name="读取文档资源文本", description="读取当前用户可见（无需已经打开）文档资源的转换或抽取文本。"),
        output_cache_spec=OutputCacheSpecMeta(strategy=OutputCacheStrategy.LONG_TEXT, cache_paths=("text",)),
        failure_reason="Document Resource Text Read Failed",
    ))
    @mcp.tool(
        name="read_document_resource_text",
        description=(
            "Read the latest converted or extracted text for one current-user-visible Wisepen document resource. "
            "Supported resource types are PDF, DOC, DOCX, PPT, PPTX, XLS, and XLSX. For Note resources, use "
            "read_note_resource_text."
        ),
    )
    async def read_document_resource_text(
        resource_id: Annotated[str, Field(min_length=1, description="Document resource id returned by search_resources or selected by the user.")],
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
        if resource_info.resource_type not in _DOCUMENT_RESOURCE_TYPES:
            raise ServiceException(McpErrorCode.RESOURCE_REQUEST_INVALID, f"unsupported document resource type: {resource_info.resource_type}")

        text_payload = await document_client.get_search_text(resource_id)
        return {
            "resource_id": resource_id,
            "resource_type": resource_info.resource_type.name,
            "resource_name": resource_info.resource_name,
            "content_type": "text/markdown",
            "text": text_payload.search_text,
        }


__all__ = ["register_read_document_resource_text_tool"]
