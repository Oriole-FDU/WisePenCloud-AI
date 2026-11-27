from datetime import datetime, timezone
from enum import Enum

from beanie import Document
from pydantic import Field
from pymongo import IndexModel, ASCENDING


class MessageFeedback(str, Enum):
    """消息反馈类型（点赞 / 点踩 / 取消）"""
    LIKE = "like"
    DISLIKE = "dislike"
    NONE = "none"


class ChatMessageFeedback(Document):
    """消息点赞/点踩记录（仅存储，供前端恢复当前用户反馈状态）"""
    user_id: str
    session_id: str
    message_id: str  # assistant UIMessage 的 id（即该回合最后一条 assistant 消息 id）
    feedback: MessageFeedback
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "wisepen_chat_message_feedback"
        indexes = [
            IndexModel([("user_id", ASCENDING), ("message_id", ASCENDING)], unique=True),
            IndexModel([("session_id", ASCENDING), ("message_id", ASCENDING)]),
        ]
