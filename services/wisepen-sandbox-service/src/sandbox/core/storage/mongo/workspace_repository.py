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

    async def set_export_bundle(
        self,
        workspace_id: str,
        export_bundle: WorkspaceExportBundleRef | None,
    ) -> SessionWorkspaceDocument | None:
        return await SessionWorkspaceDocument.find_one(
            SessionWorkspaceDocument.id == workspace_id,
        ).update(
            {
                "$set": {
                    "export_bundle": export_bundle,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

    async def change_state(
        self,
        workspace_id: str,
        state: WorkspaceState,
    ) -> SessionWorkspaceDocument | None:
        return await SessionWorkspaceDocument.find_one(
            SessionWorkspaceDocument.id == workspace_id,
        ).update(
            {
                "$set": {
                    "state": state,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            response_type=UpdateResponse.NEW_DOCUMENT,
        )
