from typing import Literal
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """进程存活探针响应。"""
    status: Literal["ok"] = Field(..., description="进程存活状态。")