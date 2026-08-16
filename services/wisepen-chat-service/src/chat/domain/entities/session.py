from datetime import datetime, timezone
from typing import List, Optional
from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel, ASCENDING, DESCENDING

class AttachmentRef(BaseModel):
    """附件"""
    attachment_id: str
    attachment_name: str
    deleted: bool = False


class TemporaryAttachmentRef(AttachmentRef):
    """临时附件引用"""
    object_key: str = ""
    extension: str
    file_size: int
    mime_type: Optional[str] = None


class ResourceAttachmentRef(AttachmentRef):
    """文档库资源引用"""
    resource_id: str
    resource_type: str


class ChatSession(Document):
    """会话实体（Beanie Document，映射到 chat_sessions 集合）"""
    user_id: str
    title: str = "New Chat"
    is_pinned: bool = False
    pinned_at: Optional[datetime] = None
    temporary_attachment_refs: List[TemporaryAttachmentRef] = Field(default_factory=list)
    resource_attachment_refs: List[ResourceAttachmentRef] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    current_summary: Optional[str] = None
    summary_updated_at: Optional[datetime] = None
    agent_id: Optional[str] = None
    agent_version: Optional[int] = None
    last_selected_skill_ids: List[str] = Field(default_factory=list)
    last_tool_selection_default_enabled: Optional[bool] = None
    last_tool_selection_overrides: dict[str, bool] = Field(default_factory=dict)

    class Settings:
        name = "wisepen_chat_session"  # MongoDB 集合名
        indexes = [
            # 按用户列出会话列表的核心查询路径，防全表扫描
            IndexModel([("user_id", ASCENDING), ("updated_at", DESCENDING)]),
        ]
