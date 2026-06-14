from datetime import datetime, timezone
from typing import Optional, List
from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel, ASCENDING, DESCENDING


class AttachmentMeta(BaseModel):
    """附件元数据（嵌入 ChatSession.attachments 列表）"""
    object_key: str
    oss_object_key: str = ""
    original_name: str
    extension: str
    file_size: int
    mime_type: Optional[str] = None
    deleted: bool = False


class ResourceRef(BaseModel):
    """文档库资源引用（嵌入 ChatSession.resource_refs 列表）"""
    resource_id: str
    resource_type: str
    loaded_at: Optional[datetime] = None
    deleted: bool = False


class ChatSession(Document):
    """会话实体（Beanie Document，映射到 chat_sessions 集合）"""
    user_id: str
    title: str = "New Chat"
    is_pinned: bool = False
    pinned_at: Optional[datetime] = None
    attachments: List[AttachmentMeta] = Field(default_factory=list)
    resource_refs: List[ResourceRef] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    current_summary: Optional[str] = None
    summary_updated_at: Optional[datetime] = None
    agent_id: Optional[str] = None
    agent_version: Optional[int] = None

    class Settings:
        name = "wisepen_chat_session"  # MongoDB 集合名
        indexes = [
            # 按用户列出会话列表的核心查询路径，防全表扫描
            IndexModel([("user_id", ASCENDING), ("updated_at", DESCENDING)]),
        ]
