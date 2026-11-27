from datetime import datetime, timezone
from enum import Enum
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


class SessionStatus(str, Enum):
    """会话当前状态（面向会话列表/详情展示）"""
    GENERATING = "generating"                             # 正在生成
    AWAITING_USER_RESPONSE = "awaiting_user_response"     # 等待用户响应（等审批/客户端工具，超时前）
    SUSPENDED = "suspended"                               # 会话挂起（等前端响应超时后）
    COMPLETED_UNREAD = "completed_unread"                 # 生成完成（用户未读）
    ERROR_UNREAD = "error_unread"                         # 生成异常（用户未读）
    NORMAL = "normal"                                     # 正常


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
    context_tokens: int = 0  # 最近一次 LLM 调用的输入 token 数，作为当前上下文占用指示
    # 持久化的会话状态（仅 NORMAL / COMPLETED_UNREAD / ERROR_UNREAD 落库；
    # GENERATING / AWAITING_USER_RESPONSE / SUSPENDED 由运行时派生，不落库）
    status: SessionStatus = SessionStatus.NORMAL
    last_turn_error: Optional[str] = None  # 最近一次生成异常的简述，供列表/详情展示

    class Settings:
        name = "wisepen_chat_session"  # MongoDB 集合名
        indexes = [
            # 按用户列出会话列表的核心查询路径，防全表扫描
            IndexModel([("user_id", ASCENDING), ("updated_at", DESCENDING)]),
        ]
