from __future__ import annotations

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from common.core.domain import R
from sandbox_v1.api.schemas import PoolMetricsResponse
from sandbox_v1.application.services.sandbox_pool import SandboxPool
from sandbox_v1.container import Container


router = APIRouter(prefix="/internal", tags=["pool"])


@router.get(
    "/pool/metrics",
    response_model=R[PoolMetricsResponse],
    status_code=200,
    summary="查询沙箱池指标",
    description="""
- 用途：查询预热池容量、生命周期状态计数和运行指标，用于内部监控与运维诊断。
- 请求：无请求参数。
- 约束：该接口只返回服务内部指标，不返回工作区内容、AIO token 或 Docker container ID。
- 处理：读取当前 Pool 快照；固定字段描述池代数、READY 阈值和 checkout 情况，其他已注册指标随 data 返回。
- 失败：Pool 或 Repository 读取失败 -> ResultCode.SYSTEM_ERROR。
- 响应：返回 `R[PoolMetricsResponse]`；成功时 code 为 200，指标位于 data。
""",
)
@inject
async def metrics(
    pool: SandboxPool = Depends(Provide[Container.pool]),
) -> R[PoolMetricsResponse]:
    return R.success(data=PoolMetricsResponse.from_snapshot(await pool.snapshot()))
