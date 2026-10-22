from datetime import datetime, timezone

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


class SessionWorkspaceDocument(Document):
    """Session Workspace 记录"""

    id: str = Field(..., description="工作区 ID")
    user_id: str = Field(..., description="所属用户 ID")
    session_id: str = Field(..., description="所属会话 ID")

    sandbox_id: str | None = Field(default=None, description="当前关联的沙箱 ID")
    workspace_path: str | None = Field(default=None, description="容器内 workspace 的物理路径")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="创建时间")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="最近更新时间")

    class Settings:
        name = "wisepen_sandbox_session_workspace"
        indexes = [
            IndexModel(
                [("user_id", ASCENDING), ("session_id", ASCENDING), ("updated_at", DESCENDING)],
                name="idx_user_session_updated",
            ),
            IndexModel([("sandbox_id", ASCENDING)], name="idx_workspace_sandbox"),
        ]
