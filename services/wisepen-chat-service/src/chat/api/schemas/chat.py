from typing import Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """
    [DTO] 聊天请求传输对象。
    """
    query: str = Field(..., min_length=1, description="用户的输入内容")
    session_id: str = Field(..., description="会话ID，用于检索记忆")

    # None 表示使用服务端 settings.DEFAULT_MODEL；
    # 前端可显式传入以覆盖（"请求 > 配置" 优先级链的请求层）
    model: Optional[str] = Field(default=None, description="指定使用的模型，不传则使用服务端默认配置")

    selected_text: Optional[str] = Field(default=None, description="用户选择的原文")

    model_config = {"extra": "ignore"}
