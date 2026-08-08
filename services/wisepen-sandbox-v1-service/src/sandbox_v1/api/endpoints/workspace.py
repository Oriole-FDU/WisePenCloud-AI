from __future__ import annotations

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from common.core.domain import R
from sandbox_v1.api.schemas import WorkspaceLifecycleResponse
from sandbox_v1.application.services.workspace_service import WorkspaceService
from sandbox_v1.container import Container


router = APIRouter(prefix="/internal/workspaces", tags=["workspace"])


class WorkspaceRequest(BaseModel):
    """工作区生命周期操作请求。"""

    user_id: str = Field(..., min_length=1, description="用户 ID")
    session_id: str = Field(..., min_length=1, description="会话 ID")


@router.post(
    "/deleteWorkspace",
    response_model=R[WorkspaceLifecycleResponse],
    status_code=200,
    summary="删除工作区",
    description="""
- 用途：逻辑删除指定用户会话对应的工作区。
- 请求：请求体必须提供 user_id 和 session_id，用于定位目标工作区。
- 约束：删除前会先创建工作区快照；该操作不会永久删除快照数据。
- 处理：保存可恢复的 tombstone 快照，删除受管工作区目录，并返回 workspace_deleted 状态。
- 失败：请求参数校验失败 -> ResultCode.PARAM_ERROR；工作区快照不支持当前文件 -> SandboxErrorCode.WORKSPACE_SNAPSHOT_REJECTED；工作区路径不安全 -> SandboxErrorCode.WORKSPACE_PATH_UNSAFE。
- 响应：返回 R[WorkspaceLifecycleResponse]；成功时 code 为 200，生命周期结果位于 data。
""",
)
@inject
async def logical_delete(
    request: WorkspaceRequest,
    workspace_service: WorkspaceService = Depends(Provide[Container.workspace_service]),
) -> R[WorkspaceLifecycleResponse]:
    result = await workspace_service.logical_delete(
        user_id=request.user_id,
        session_id=request.session_id,
    )
    return R.success(data=WorkspaceLifecycleResponse.from_result(result))


@router.post(
    "/rebuildWorkspace",
    response_model=R[WorkspaceLifecycleResponse],
    status_code=200,
    summary="重建工作区",
    description="""
- 用途：显式重建指定用户会话对应的工作区。
- 请求：请求体必须提供 user_id 和 session_id，用于定位目标工作区。
- 约束：存在可恢复快照时优先恢复快照；没有可用快照时创建空工作区。
- 处理：恢复工作区目录并更新生命周期状态；同一工作区并发重建时返回 workspace_restoring。
- 失败：请求参数校验失败 -> ResultCode.PARAM_ERROR；工作区路径不安全 -> SandboxErrorCode.WORKSPACE_PATH_UNSAFE；快照恢复失败 -> ResultCode.SYSTEM_ERROR。
- 响应：返回 R[WorkspaceLifecycleResponse]；成功时 code 为 200，生命周期结果位于 data。
""",
)
@inject
async def rebuild(
    request: WorkspaceRequest,
    workspace_service: WorkspaceService = Depends(Provide[Container.workspace_service]),
) -> R[WorkspaceLifecycleResponse]:
    result = await workspace_service.rebuild(
        user_id=request.user_id,
        session_id=request.session_id,
    )
    return R.success(data=WorkspaceLifecycleResponse.from_result(result))
