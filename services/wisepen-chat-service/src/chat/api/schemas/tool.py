from typing import Any, Optional

from pydantic import BaseModel, Field

from chat.domain.entities.mcp_tool_server_config import McpToolStatus


class ToolResponse(BaseModel):
    name: str
    description: str
    requires_config: bool
    configured: bool
    enabled: bool
    missing_config_keys: list[str] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    secret_fingerprints: dict[str, str] = Field(default_factory=dict)


class ListUserToolsResponse(BaseModel):
    tools: list[ToolResponse] = Field(default_factory=list)


class UpdateUserToolConfigRequest(BaseModel):
    tool_name: str
    enabled: Optional[bool] = None
    config: Optional[dict[str, Any]] = None
    secret_config: Optional[dict[str, str]] = None


class DeleteUserToolConfigRequest(BaseModel):
    tool_name: str


class McpToolSnapshotResponse(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    status: McpToolStatus = McpToolStatus.AVAILABLE


class UserMcpServerResponse(BaseModel):
    server_id: str
    display_name: str = ""
    url: str
    enabled: bool
    headers: dict[str, str] = Field(default_factory=dict)
    secret_header_fingerprints: dict[str, str] = Field(default_factory=dict)
    enabled_tool_names: list[str] = Field(default_factory=list)


class ListUserMcpServersResponse(BaseModel):
    servers: list[UserMcpServerResponse] = Field(default_factory=list)


class PreviewUserMcpServerRequest(BaseModel):
    display_name: str = ""
    url: str
    enabled: bool = True
    headers: dict[str, str] = Field(default_factory=dict)
    secret_headers: dict[str, str] = Field(default_factory=dict)
    enabled_tool_names: list[str] = Field(default_factory=list)


class PreviewUserMcpServerResponse(BaseModel):
    status: McpToolStatus
    error: str = ""
    tools: list[McpToolSnapshotResponse] = Field(default_factory=list)


class UpsertUserMcpServerRequest(BaseModel):
    server_id: Optional[str] = None
    display_name: str = ""
    url: str
    enabled: bool = True
    headers: dict[str, str] = Field(default_factory=dict)
    secret_headers: Optional[dict[str, str]] = None
    enabled_tool_names: list[str] = Field(default_factory=list)


class DeleteUserMcpServerRequest(BaseModel):
    server_id: str
