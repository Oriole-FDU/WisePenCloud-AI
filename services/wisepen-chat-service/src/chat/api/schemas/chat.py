from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from chat.domain.entities import AttachmentContextMode


class AttachmentRefRequest(BaseModel):
    """聊天请求里的附件引用"""

    attachment_id: str = Field(..., description="附件 ID")
    enabled: bool = Field(default=True, description="是否参与本轮")
    context_mode: AttachmentContextMode = Field(
        default=AttachmentContextMode.SUMMARY,
        description="附件上下文注入模式",
    )


class ChatRequest(BaseModel):
    """
    聊天请求传输对象
    """
    session_id: str = Field(..., description="会话ID")
    query: str = Field(..., description="用户问题")
    model: Optional[str] = Field(default=None, description="模型ID")
    provider_id: Optional[str] = Field(default=None, description="指定供应商ID")
    states: Optional[List[Dict[str, Any]]] = Field(default=None, description="上下文状态列表")
    model_config = {"extra": "ignore"}
