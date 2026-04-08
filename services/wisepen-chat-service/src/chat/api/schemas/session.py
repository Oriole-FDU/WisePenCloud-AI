from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict

from chat.domain.entities import ChatSession, ChatMessage


class CreateSessionRequest(BaseModel):
    title: Optional[str] = Field(default="New Chat", description="会话标题")

class RenameSessionRequest(BaseModel):
    new_title: Optional[str] = Field(default=None, description="新会话标题")

class PinSessionRequest(BaseModel):
    set_pin: bool = Field(default=False, description="是否置顶")


class SessionResponse(BaseModel):
    id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str

    @classmethod
    def from_entity(cls, session: ChatSession) -> "SessionResponse":
        return cls(
            id=str(session.id) if session.id else "",
            user_id=session.user_id,
            title=session.title,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
        )


class MessageResponse(BaseModel):
    """
    会话消息条目响应。
    - user / assistant 消息：完整返回 content 和 tool_calls。
    - TOOL role（工具调用结果）：在仓储层已过滤，不会出现在此处。
    """
    id: str
    role: str
    content: Optional[str]
    tool_calls: Optional[List[Dict[str, Any]]]
    created_at: str

    @classmethod
    def from_entity(cls, msg: ChatMessage) -> "MessageResponse":
        return cls(
            id=str(msg.id) if msg.id else "",
            role=msg.role.value,
            content=msg.content,
            tool_calls=msg.tool_calls,
            created_at=msg.created_at.isoformat(),
        )