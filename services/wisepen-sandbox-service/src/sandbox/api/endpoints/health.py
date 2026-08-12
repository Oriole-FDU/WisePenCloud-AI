from __future__ import annotations
from fastapi import APIRouter
from common.core.domain import R
from sandbox.api.schemas import HealthResponse


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