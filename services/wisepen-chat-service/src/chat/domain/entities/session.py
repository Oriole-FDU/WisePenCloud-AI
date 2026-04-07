from datetime import datetime, timezone
from typing import Optional
from beanie import Document
from pydantic import Field
from pymongo import IndexModel, ASCENDING, DESCENDING


class ChatSession(Document):
    """会话实体（Beanie Document，映射到 chat_sessions 集合）"""
    user_id: str
    title: str = "New Chat"
    is_pinned: bool = False
    pinned_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    current_summary: Optional[str] = None
    summary_updated_at: Optional[datetime] = None

    class Settings:
        name = "wisepen_chat_session"  # MongoDB 集合名
        indexes = [
            # 按用户列出会话列表的核心查询路径，防全表扫描
            IndexModel([("user_id", ASCENDING), ("updated_at", DESCENDING)]),
        ]
