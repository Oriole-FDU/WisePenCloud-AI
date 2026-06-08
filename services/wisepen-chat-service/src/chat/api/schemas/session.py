from typing import Any, List, Optional

from pydantic import BaseModel, Field

from chat.domain.entities import ChatSession


class CreateSessionRequest(BaseModel):
    title: Optional[str] = Field(default="New Chat", description="会话标题")


class RenameSessionRequest(BaseModel):
    new_title: Optional[str] = Field(default=None, description="新会话标题")


class PinSessionRequest(BaseModel):
    set_pin: bool = Field(default=False, description="是否置顶")


class RollbackSessionRequest(BaseModel):
    message_id: str = Field(description="回滚锚点消息 ID，会删除该消息及其之后的消息")


class SessionResponse(BaseModel):
    id: str
    user_id: str
    title: str
    is_pinned: bool
    pinned_at: Optional[str] = None
    created_at: str
    updated_at: str

    @classmethod
    def from_entity(cls, session: ChatSession) -> "SessionResponse":
        return cls(
            id=str(session.id) if session.id else "",
            user_id=session.user_id,
            title=session.title,
            is_pinned=session.is_pinned,
            pinned_at=session.pinned_at.isoformat() if session.pinned_at else None,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
        )


class UIMessagePartResponse(BaseModel):
    """Vercel AI SDK 6.x UIMessage 的单个 part"""

    type: str
    text: Optional[str] = None
    state: Optional[str] = None
    toolCallId: Optional[str] = None
    input: Optional[Any] = None
    output: Optional[Any] = None


class UIMessageResponse(BaseModel):
    """
    Vercel AI SDK 6.x UIMessage 格式，用于 initialMessages。
    所有内容（文本、推理、工具调用）均在 parts 数组中按顺序排列。
    """

    id: str
    role: str
    parts: List[UIMessagePartResponse]
    createdAt: Optional[str] = None
