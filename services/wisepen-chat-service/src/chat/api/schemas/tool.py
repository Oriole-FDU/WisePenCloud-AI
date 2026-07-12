from typing import Any, Optional

from pydantic import BaseModel, Field


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
