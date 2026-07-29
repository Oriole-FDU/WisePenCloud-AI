from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """进程存活探针响应。"""

    status: Literal["ok"] = Field(..., description="进程存活状态。")


class ReadinessResponse(BaseModel):
    """Pool 达到最低 READY 数量后的就绪探针响应。"""

    status: Literal["ready"] = Field(..., description="服务就绪状态。")
    ready: int = Field(..., ge=0, description="当前 READY 实例数。")
    min_ready: int = Field(..., ge=0, description="要求的最低 READY 实例数。")


class ReadinessErrorDetail(BaseModel):
    """readiness 未满足时的错误详情。"""

    code: Literal["MIN_READY_NOT_REACHED"] = Field(..., description="未就绪原因。")
    ready: int = Field(..., ge=0, description="当前 READY 实例数。")
    min_ready: int = Field(..., ge=0, description="要求的最低 READY 实例数。")


class ReadinessErrorResponse(BaseModel):
    """FastAPI HTTPException 的 readiness 错误响应。"""

    detail: ReadinessErrorDetail
