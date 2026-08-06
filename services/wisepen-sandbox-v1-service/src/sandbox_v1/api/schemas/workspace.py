from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from sandbox_v1.domain.entities import (
    WorkspaceLifecycleResult,
    WorkspaceLifecycleStatus,
)


class WorkspaceLifecycleResponse(BaseModel):
    """Response for Chat-initiated Workspace lifecycle operations."""

    model_config = ConfigDict(use_enum_values=True)

    user_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    status: WorkspaceLifecycleStatus
    workspace_path: str | None = None
    snapshot_id: str | None = None
    restored_from_snapshot: bool = False
    unrecoverable_reason: str | None = None

    @classmethod
    def from_result(
        cls,
        result: WorkspaceLifecycleResult,
    ) -> "WorkspaceLifecycleResponse":
        return cls(
            user_id=result.user_id,
            session_id=result.session_id,
            status=result.status,
            workspace_path=result.workspace_path,
            snapshot_id=result.snapshot_id,
            restored_from_snapshot=result.restored_from_snapshot,
            unrecoverable_reason=result.unrecoverable_reason,
        )
