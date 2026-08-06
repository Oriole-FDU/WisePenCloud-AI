from __future__ import annotations

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from common.core.domain import R
from sandbox_v1.api.schemas import WorkspaceLifecycleResponse
from sandbox_v1.application.services.workspace_service import WorkspaceService
from sandbox_v1.container import Container


router = APIRouter(prefix="/internal/workspaces", tags=["workspace"])


@router.post(
    "/{user_id}/{session_id}/delete",
    response_model=R[WorkspaceLifecycleResponse],
    status_code=200,
    summary="Logically delete a Workspace",
    description="""
Chat-only control operation. The service snapshots the managed Workspace
directory before removing it, stores a recoverable tombstone, and returns
workspace_deleted. Permanent deletion is intentionally left to the next phase.
""",
)
@inject
async def logical_delete(
    user_id: str,
    session_id: str,
    workspace_service: WorkspaceService = Depends(Provide[Container.workspace_service]),
) -> R[WorkspaceLifecycleResponse]:
    result = await workspace_service.logical_delete(
        user_id=user_id,
        session_id=session_id,
    )
    return R.success(data=WorkspaceLifecycleResponse.from_result(result))


@router.post(
    "/{user_id}/{session_id}/rebuild",
    response_model=R[WorkspaceLifecycleResponse],
    status_code=200,
    summary="Explicitly rebuild a Workspace",
    description="""
Chat-only control operation. A deleted Workspace is restored from its
tombstone snapshot when recoverable, otherwise an empty Workspace is created.
Concurrent rebuild of the same Workspace returns workspace_restoring.
""",
)
@inject
async def rebuild(
    user_id: str,
    session_id: str,
    workspace_service: WorkspaceService = Depends(Provide[Container.workspace_service]),
) -> R[WorkspaceLifecycleResponse]:
    result = await workspace_service.rebuild(
        user_id=user_id,
        session_id=session_id,
    )
    return R.success(data=WorkspaceLifecycleResponse.from_result(result))
