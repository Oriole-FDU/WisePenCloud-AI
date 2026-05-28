from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class AttachmentRefRequest(BaseModel):
    """AI tool call 使用的附件引用"""

    object_key: str = Field(..., description="OSS 对象键")
    filename: str = Field(default="", description="文件名")


class ChatRequest(BaseModel):
    """
    聊天请求传输对象
    """
    session_id: str = Field(..., description="会话ID")
    query: str = Field(..., description="用户问题")
    model: Optional[str] = Field(default=None, description="模型ID")
    provider_id: Optional[str] = Field(default=None, description="指定供应商ID")
    states: Optional[List[Dict[str, Any]]] = Field(default=None, description="上下文状态列表")
    attachment_refs: Optional[List[AttachmentRefRequest]] = Field(default=None, description="附件引用列表")
    model_config = {"extra": "ignore"}
