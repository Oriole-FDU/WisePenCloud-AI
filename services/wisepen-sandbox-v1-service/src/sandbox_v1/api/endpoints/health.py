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


@router.get(
    "/ready",
    response_model=R[ReadinessResponse],
    responses={
        503: {
            "model": ReadinessErrorResponse,
            "description": "READY 实例数低于 min_ready，服务暂不能承接新的分配请求。",
        }
    },
    summary="检查服务就绪状态",
    description="""
- 用途：确认预热池中有足够 READY 实例承接新的沙箱租约。
- 请求：无请求参数；就绪阈值来自 Pool 配置。
- 约束：READY 数量必须达到 min_ready；进程存活但 Pool 未达到阈值时仍视为未就绪。
- 处理：读取 Pool 快照并比较当前 READY 数量与 min_ready，不创建或修改沙箱实例。
- 失败：READY 数量不足 -> HTTP 503，detail.code 为 `MIN_READY_NOT_REACHED`。
- 响应：就绪时返回 status、ready 和 min_ready，HTTP 200。
""",
)
@inject
async def ready(
    pool: SandboxPool = Depends(Provide[Container.pool]),
) -> ReadinessResponse:
    snapshot = await pool.snapshot()
    ready = snapshot.counts.get(SandboxState.READY, 0)
    if ready < snapshot.min_ready:
        # 就绪状态代表是否有足够预热实例承接新请求，不等同于进程存活。
        raise ServiceException(SandboxErrorCode.POOL_EMPTY)
    return R.success(data=ReadinessResponse(
        status = "ready",
        ready = ready,
        min_ready = snapshot.min_ready,
    ))
