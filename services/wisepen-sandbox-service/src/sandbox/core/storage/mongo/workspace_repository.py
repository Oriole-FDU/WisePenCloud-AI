from __future__ import annotations

from datetime import datetime, timezone

from beanie import UpdateResponse

from sandbox.domain.entities import (
    SessionWorkspaceDocument,
    WorkspaceExportBundleRef,
    WorkspaceState,
)
from sandbox.domain.repositories import WorkspaceRepository


class MongoWorkspaceRepository(WorkspaceRepository):
    """SessionWorkspaceDocument 的 MongoDB 仓储实现。"""

    async def save(self, workspace: SessionWorkspaceDocument) -> None:
        await workspace.save()

    async def get_by_user_session(
        self,
        user_id: str,
        session_id: str,
    ) -> SessionWorkspaceDocument | None:
        return await SessionWorkspaceDocument.find_one(
            SessionWorkspaceDocument.user_id == user_id,
            SessionWorkspaceDocument.session_id == session_id,
            sort=[("updated_at", -1)],
        )

    async def get_by_id(
        self,
        workspace_id: str,
    ) -> SessionWorkspaceDocument | None:
        return await SessionWorkspaceDocument.find_one(
            SessionWorkspaceDocument.id == workspace_id,
        )

    async def set_new_workspace_path(
        self,
        workspace_id: str,
        workspace_path: str,
    ) -> SessionWorkspaceDocument | None:
        return await SessionWorkspaceDocument.find_one(
            SessionWorkspaceDocument.id == workspace_id,
        ).update(
            {
                "$set": {
                    "workspace_path": workspace_path,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

    async def set_attached_workspace(
        self,
        workspace_id: str,
        sandbox_id: str,
        workspace_path: str,
    ) -> SessionWorkspaceDocument | None:
        now = datetime.now(timezone.utc)
        return await SessionWorkspaceDocument.find_one(
            SessionWorkspaceDocument.id == workspace_id,
        ).update(
            {
                "$set": {
                    "sandbox_id": sandbox_id,
                    "workspace_path": workspace_path,
                    "state": WorkspaceState.ATTACHED,
                    "updated_at": now,
                    "last_accessed_at": now,
                }
            },
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

    async def list_attached_by_sandbox(
        self,
        sandbox_id: str,
    ) -> list[SessionWorkspaceDocument]:
        return await SessionWorkspaceDocument.find(
            {"sandbox_id": sandbox_id, "state": WorkspaceState.ATTACHED},
        ).to_list()

    async def list_idle_attached(
        self,
        cutoff: datetime,
        limit: int,
    ) -> list[SessionWorkspaceDocument]:
        if limit <= 0:
            return []
        return await SessionWorkspaceDocument.find(
            {
                "state": WorkspaceState.ATTACHED,
                "last_accessed_at": {"$lte": cutoff},
            },
            sort=[("last_accessed_at", 1)],
        ).limit(limit).to_list()

    async def touch_if_attached(
        self,
        workspace_id: str,
    ) -> SessionWorkspaceDocument | None:
        now = datetime.now(timezone.utc)
        return await SessionWorkspaceDocument.find_one(
            {
                "id": workspace_id,
                "state": WorkspaceState.ATTACHED,
                "sandbox_id": {"$ne": None},
                "workspace_path": {"$ne": None},
            },
        ).update(
            {
                "$set": {
                    "last_accessed_at": now,
                    "updated_at": now,
                }
            },
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

    async def count_runtime_workspaces(
        self,
        sandbox_id: str,
    ) -> int:
        return await SessionWorkspaceDocument.find(
            {
                "sandbox_id": sandbox_id,
                "state": {
                    "$in": [
                        WorkspaceState.ATTACHED,
                        WorkspaceState.EXPORTING,
                        WorkspaceState.IMPORTING,
                    ]
                },
            }
        ).count()

    async def change_state(
        self,
        workspace_id: str,
        state: WorkspaceState,
        expected_state: WorkspaceState | None = None,
        *,
        export_bundle: WorkspaceExportBundleRef | None = None,
        clear_runtime_binding: bool = False,
    ) -> SessionWorkspaceDocument | None:
        filters: dict[str, object] = {"id": workspace_id}
        if expected_state is not None:
            filters["state"] = expected_state
        updates: dict[str, object] = {
            "state": state,
            "updated_at": datetime.now(timezone.utc),
        }
        if export_bundle is not None:
            updates["export_bundle"] = export_bundle.model_dump()
        if clear_runtime_binding:
            updates.update({"sandbox_id": None, "workspace_path": None})
        return await SessionWorkspaceDocument.find_one(
            filters,
        ).update(
            {
                "$set": updates,
            },
            response_type=UpdateResponse.NEW_DOCUMENT,
        )
