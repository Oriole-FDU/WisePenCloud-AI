from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GetSpeechCredentialRequest(BaseModel):
    provider: str = Field(default="IFLYTEK", description="语音识别 Provider")
    options: dict[str, Any] = Field(default_factory=dict, description="Provider 自定义请求参数")


class SpeechCredentialResponse(BaseModel):
    provider: str = Field(..., description="语音识别 Provider")
    expires_at: datetime = Field(..., description="凭证建议失效时间")
    credential: dict[str, Any] = Field(..., description="Provider 自定义鉴权参数")
