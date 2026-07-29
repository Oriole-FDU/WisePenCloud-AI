from __future__ import annotations

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Body, Depends, Path

from common.core.domain import R
from sandbox.api.schemas import (
    AllocateRequest,
    ExecuteRequest,
    ExecutionResultResponse,
    ReleaseRequest,
    ReleaseResponse,
    SandboxLeaseResponse,
    SandboxStatusResponse,
)
from sandbox.application.services.sandbox_scheduler import SandboxScheduler
from sandbox.container import Container
from sandbox.domain.entities import ExecutionRequest


router = APIRouter(prefix="/internal", tags=["sandbox"])


@router.post(
    "/sandboxes/allocate",
    response_model=R[SandboxLeaseResponse],
    status_code=200,
    summary="分配沙箱租约",
    description="""
- 用途：从 READY 预热池中分配一个沙箱，为 Chat、MCP 或 VNC 创建短期租约。
- 请求：request_id 用于幂等分配；tenant_id 和 workspace_id 定义租约的隔离范围。
- 约束：三个标识不能为空；tenant_id 和 workspace_id 只能包含字母、数字、下划线和连字符；相同 request_id 携带不同租户或工作区会被拒绝。
- 处理：原子地 checkout READY 实例，恢复工作区快照，激活沙箱并返回 lease、fencing token 和访问 endpoint；重试相同 request_id 时返回原租约。
- 失败：请求参数校验失败 -> ResultCode.PARAM_ERROR；无 READY 实例 -> SandboxErrorCode.POOL_EMPTY；幂等上下文冲突 -> SandboxErrorCode.REQUEST_CONFLICT；激活或 Provider 调用失败 -> SandboxErrorCode.SANDBOX_UNAVAILABLE。
- 响应：返回 `R[SandboxLeaseResponse]`；Chat 后续 execute/release 使用 data 中的 lease_id 和 fencing_token。
""",
)
@inject
async def allocate(
    body: AllocateRequest,
    scheduler: SandboxScheduler = Depends(Provide[Container.scheduler]),
) -> R[SandboxLeaseResponse]:
    lease = await scheduler.allocate(body.request_id, body.tenant_id, body.workspace_id)
    return R.success(data=SandboxLeaseResponse.from_entity(lease))


@router.post(
    "/leases/{lease_id}/execute",
    response_model=R[ExecutionResultResponse],
    status_code=200,
    summary="执行沙箱操作",
    description="""
- 用途：通过有效租约向当前沙箱转发一次文件、Shell 或代码执行操作。
- 请求：路径参数 lease_id 指定租约；request_id 标识本次操作；tenant_id、workspace_id 和 fencing_token 用于租约校验；operation 和 payload 描述具体操作。
- 约束：租约必须存在、未过期且处于运行状态；请求中的租户、工作区和 fencing_token 必须与租约一致；operation 不能为空且 payload 必须为对象。
- 处理：校验租约上下文后调用 SandboxProvider，Provider 负责把 operation 映射到具体运行时协议。
- 失败：请求参数校验失败 -> ResultCode.PARAM_ERROR；租约不存在 -> SandboxErrorCode.LEASE_NOT_FOUND；租约过期 -> SandboxErrorCode.LEASE_EXPIRED；fencing 校验失败 -> SandboxErrorCode.FENCING_REJECTED；租约不可运行或 Provider 执行失败 -> SandboxErrorCode.SANDBOX_UNAVAILABLE。
- 响应：返回 `R[ExecutionResultResponse]`，data 包含本次请求 ID、执行状态和 Provider 结果。
""",
)
@inject
async def execute(
    lease_id: str = Path(..., min_length=1, max_length=200, description="沙箱租约 ID。"),
    body: ExecuteRequest = Body(...),
    scheduler: SandboxScheduler = Depends(Provide[Container.scheduler]),
) -> R[ExecutionResultResponse]:
    result = await scheduler.execute(
        lease_id,
        ExecutionRequest(
            request_id=body.request_id,
            tenant_id=body.tenant_id,
            workspace_id=body.workspace_id,
            operation=body.operation,
            payload=body.payload,
            fencing_token=body.fencing_token,
        ),
    )
    return R.success(data=ExecutionResultResponse.from_entity(result))


@router.post(
    "/leases/{lease_id}/release",
    response_model=R[ReleaseResponse],
    status_code=200,
    summary="释放沙箱租约",
    description="""
- 用途：关闭当前租约，提交工作区快照并销毁用户沙箱实例。
- 请求：路径参数 lease_id 指定租约；fencing_token 用于拒绝旧租约释放请求。
- 约束：fencing_token 必须为正整数；释放操作具备幂等语义，重复释放不会重复提交或销毁。
- 处理：先关闭租约入口，再导出并提交工作区，最后销毁实例；工作区提交失败时仍继续销毁，用户实例不会回到 READY 池。
- 失败：请求参数校验失败 -> ResultCode.PARAM_ERROR；租约不存在 -> SandboxErrorCode.LEASE_NOT_FOUND；租约过期 -> SandboxErrorCode.LEASE_EXPIRED；fencing 校验失败 -> SandboxErrorCode.FENCING_REJECTED；工作区提交失败 -> SandboxErrorCode.WORKSPACE_SYNC_FAILED。
- 响应：返回 `R[ReleaseResponse]`，成功时 data.status 为 `released`。
""",
)
@inject
async def release(
    lease_id: str = Path(..., min_length=1, max_length=200, description="沙箱租约 ID。"),
    body: ReleaseRequest = Body(...),
    scheduler: SandboxScheduler = Depends(Provide[Container.scheduler]),
) -> R[ReleaseResponse]:
    await scheduler.release(lease_id, body.fencing_token)
    return R.success(data=ReleaseResponse(status="released"))


@router.get(
    "/sandboxes/{sandbox_id}",
    response_model=R[SandboxStatusResponse],
    status_code=200,
    summary="查询沙箱状态",
    description="""
- 用途：查询指定沙箱的生命周期和租约管理状态，用于内部管理与故障诊断。
- 请求：路径参数 sandbox_id 指定沙箱实例。
- 约束：该接口只返回安全状态投影，不返回 Provider 内部 ID、metadata、endpoint token 或 readiness token。
- 处理：读取 SandboxScheduler 当前记录，并转换为稳定的 SandboxStatusResponse；不会修改沙箱生命周期。
- 失败：沙箱不存在 -> SandboxErrorCode.LEASE_NOT_FOUND；Repository 查询失败 -> ResultCode.SYSTEM_ERROR。
- 响应：返回 `R[SandboxStatusResponse]`，包含状态、时间、租约上下文和非敏感 endpoint 地址。
""",
)
@inject
async def status(
    sandbox_id: str = Path(..., min_length=1, max_length=200, description="沙箱实例 ID。"),
    scheduler: SandboxScheduler = Depends(Provide[Container.scheduler]),
) -> R[SandboxStatusResponse]:
    return R.success(data=SandboxStatusResponse.from_entity(await scheduler.status(sandbox_id)))
