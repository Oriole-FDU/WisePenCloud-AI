from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """
    [DTO] 聊天请求传输对象。
    """
    session_id: str = Field(..., description="会话ID")

    query: str = Field(..., description="用户问题")

    model: Optional[str] = Field(default=None, description="模型名称")

    states: Optional[List[Dict[str, Any]]] = Field(default=None, description="上下文状态列表")

    model_config = {"extra": "ignore"}
