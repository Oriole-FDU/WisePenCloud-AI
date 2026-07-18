from datetime import datetime, timezone
from enum import StrEnum
from typing import Dict, List, Any

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, IndexModel


class McpToolDescriptor(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)

class McpToolStatus(StrEnum):
    NEVER = "never"
    AVAILABLE = "available"
    INVALID_SCHEMA = "invalid_schema"

class McpToolSnapshot(BaseModel):
    name: str = Field(...)
    description: str = Field(default="")
    input_schema: dict[str, Any] = Field(default_factory=dict)
    status: McpToolStatus = Field(default=McpToolStatus.AVAILABLE)


class UserMcpServerConfig(Document):
    user_id: str = Field(...)
    server_id: str = Field(...)
    display_name: str = Field(default="")
    url: str = Field(...)

    enabled: bool = Field(default=True)
    headers: Dict[str, str] = Field(default_factory=dict)
    secret_headers: Dict[str, str] = Field(default_factory=dict)
    secret_header_fingerprints: Dict[str, str] = Field(default_factory=dict)
    enabled_tool_names: List[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "wisepen_user_mcp_server_configs"
        indexes = [
            IndexModel(
                [("user_id", ASCENDING), ("server_id", ASCENDING)],
                unique=True,
                name="uniq_mcp_server_user_server",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("updated_at", DESCENDING)],
                name="idx_mcp_server_user_updated",
            ),
        ]
