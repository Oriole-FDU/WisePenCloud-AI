from pydantic import BaseModel, Field
from typing import List, Optional, Any

from chat.domain.entities import ChatSession


class CreateSessionRequest(BaseModel):
    title: Optional[str] = Field(default="New Chat", description="会话标题")

class RenameSessionRequest(BaseModel):
    new_title: Optional[str] = Field(default=None, description="新会话标题")

class PinSessionRequest(BaseModel):
    set_pin: bool = Field(default=False, description="是否置顶")


class AttachmentMetaResponse(BaseModel):
    object_key: str
    original_name: str
    extension: str
    file_size: int
    mime_type: Optional[str] = None
    uploaded_at: Optional[str] = None

    @classmethod
    def from_entity(cls, meta) -> "AttachmentMetaResponse":
        return cls(
            object_key=meta.object_key,
            original_name=meta.original_name,
            extension=meta.extension,
            file_size=meta.file_size,
            mime_type=meta.mime_type,
            uploaded_at=meta.uploaded_at.isoformat() if meta.uploaded_at else None,
        )


class SessionResponse(BaseModel):
    id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str
    attachments: List[AttachmentMetaResponse] = Field(default_factory=list)

    @classmethod
    def from_entity(cls, session: ChatSession) -> "SessionResponse":
        return cls(
            id=str(session.id) if session.id else "",
            user_id=session.user_id,
            title=session.title,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            attachments=[AttachmentMetaResponse.from_entity(a) for a in session.attachments],
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