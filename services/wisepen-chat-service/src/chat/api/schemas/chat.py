from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class ChatRequest(BaseModel):
    """
    [DTO] 聊天请求传输对象。
    """

    session_id: StrictStr = Field(..., description="会话ID")

    query: StrictStr = Field(..., description="用户问题")

    model: Optional[StrictInt] = Field(default=None, description="模型ID")

    states: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="上下文状态列表"
    )

    model_config = ConfigDict(extra="forbid")
