from __future__ import annotations

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends
from common.core.domain import R
from common.core.exceptions import ServiceException
from sandbox_v1.domain.error_codes import SandboxErrorCode

from sandbox_v1.api.schemas import (
    HealthResponse,
    ReadinessErrorResponse,
    ReadinessResponse,
)
from sandbox_v1.application.services.sandbox_pool import SandboxPool
from sandbox_v1.container import Container
from sandbox_v1.domain.entities import SandboxState


router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=R[HealthResponse],
    summary="检查服务存活状态",
    description="""
- 用途：确认 Sandbox Service 进程仍在运行。
- 请求：无请求参数，也不依赖沙箱池或外部 Provider 状态。
- 处理：返回固定的 ok 状态；该接口不代表服务已经具备承接租约的 READY 实例。
- 失败：进程无法响应时由 HTTP server 或平台探针判定为不可用。
- 响应：返回 `{"status":"ok"}`，HTTP 200。
""",
)
async def health() -> R[HealthResponse]:
    return R.success(data=HealthResponse(status="ok"))