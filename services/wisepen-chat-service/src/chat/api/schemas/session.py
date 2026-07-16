from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

from chat.domain.entities import ChatSession, ResourceAttachmentRef, TemporaryAttachmentRef


class CreateSessionRequest(BaseModel):
    title: Optional[str] = Field(default="New Chat", description="会话标题")
    agent_id: Optional[str] = Field(default=None, description="绑定的 Agent 资源 ID")

class SetSessionAgentRequest(BaseModel):
    agent_id: Optional[str] = Field(default=None, description="绑定的 Agent 资源 ID")

class RenameSessionRequest(BaseModel):
    new_title: Optional[str] = Field(default=None, description="新会话标题")

class PinSessionRequest(BaseModel):
    set_pin: bool = Field(default=False, description="是否置顶")


class TemporaryAttachmentRefResponse(BaseModel):
    attachment_id: str
    attachment_type: Literal["temporary"] = "temporary"
    attachment_name: str
    object_key: str
    extension: str
    file_size: int
    mime_type: Optional[str] = None

    @classmethod
    def from_entity(cls, ref: TemporaryAttachmentRef) -> "TemporaryAttachmentRefResponse":
        return cls(
            attachment_id=ref.attachment_id,
            attachment_name=ref.attachment_name,
            object_key=ref.object_key,
            extension=ref.extension,
            file_size=ref.file_size,
            mime_type=ref.mime_type,
        )


class ResourceAttachmentRefResponse(BaseModel):
    attachment_id: str
    attachment_type: Literal["resource"] = "resource"
    attachment_name: str
    resource_id: str
    resource_type: str

    @classmethod
    def from_entity(cls, ref: ResourceAttachmentRef) -> "ResourceAttachmentRefResponse":
        return cls(
            attachment_id=ref.attachment_id,
            attachment_name=ref.attachment_name,
            resource_id=ref.resource_id,
            resource_type=ref.resource_type,
        )


class SessionResponse(BaseModel):
    id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str
    temporary_attachment_refs: List[TemporaryAttachmentRefResponse] = Field(default_factory=list)
    resource_attachment_refs: List[ResourceAttachmentRefResponse] = Field(default_factory=list)
    agent_id: Optional[str] = None
    agent_version: Optional[int] = None

    @classmethod
    def from_entity(cls, session: ChatSession) -> "SessionResponse":
        return cls(
            id=str(session.id) if session.id else "",
            user_id=session.user_id,
            title=session.title,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            temporary_attachment_refs=[
                TemporaryAttachmentRefResponse.from_entity(a)
                for a in session.temporary_attachment_refs
                if not getattr(a, "deleted", False)
            ],
            resource_attachment_refs=[
                ResourceAttachmentRefResponse.from_entity(r)
                for r in session.resource_attachment_refs
                if not getattr(r, "deleted", False)
            ],
            agent_id=session.agent_id,
            agent_version=session.agent_version,
        )


class UIMessagePartResponse(BaseModel):
    """Vercel AI SDK 6.x UIMessage 的单个 part"""
    type: str
    text: Optional[str] = None
    state: Optional[str] = None
    toolCallId: Optional[str] = None
    input: Optional[Any] = None
    output: Optional[Any] = None


class UIMessageAttachmentResponse(BaseModel):
    """用户发送消息时选中的附件快照。"""
    attachmentId: str
    filename: str
    kind: Literal["temporary", "resource"]
    available: bool


class UIMessageMetadataResponse(BaseModel):
    """允许返回给前端的消息元数据白名单。"""
    selectedAttachments: List[UIMessageAttachmentResponse] = Field(default_factory=list)


class UIMessageResponse(BaseModel):
    """
    Vercel AI SDK 6.x UIMessage 格式，用于 initialMessages。
    所有内容（文本、推理、工具调用）均在 parts 数组中按顺序排列。
    """
    id: str
    role: str
    parts: List[UIMessagePartResponse]
    metadata: Optional[UIMessageMetadataResponse] = None
    createdAt: Optional[str] = None
