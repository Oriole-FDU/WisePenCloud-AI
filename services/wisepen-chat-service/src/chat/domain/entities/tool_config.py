from datetime import datetime, timezone
from typing import Any, Dict

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

class UserToolConfig(Document):
    user_id: str = Field(...)
    tool_name: str = Field(...)

    enabled: bool = Field(default=True)
    config: Dict[str, Any] = Field(default_factory=dict)
    secret_config: Dict[str, str] = Field(default_factory=dict)
    secret_fingerprints: Dict[str, str] = Field(default_factory=dict)
    schema_version: int = Field(default=1)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "wisepen_user_tool_configs"
        indexes = [
            IndexModel(
                [("user_id", ASCENDING), ("tool_name", ASCENDING)],
                unique=True,
                name="uniq_tool_config_user_tool",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("updated_at", DESCENDING)],
                name="idx_tool_config_user_updated",
            ),
        ]
