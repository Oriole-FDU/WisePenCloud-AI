from typing import Literal

from pydantic import BaseModel, Field


class MessageFeedbackRequest(BaseModel):
    session_id: str = Field(..., min_length=1, description="目标会话 ID；必须属于当前登录用户")
    message_id: str = Field(..., min_length=1, description="目标 assistant UIMessage 的 id")
    feedback: Literal["like", "dislike", "none"] = Field(
        ...,
        description="反馈类型：like=点赞，dislike=点踩，none=取消反馈",
    )
