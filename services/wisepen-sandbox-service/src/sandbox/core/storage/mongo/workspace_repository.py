from __future__ import annotations

from datetime import datetime, timezone

from beanie import UpdateResponse

from sandbox.domain.entities import SessionWorkspaceDocument
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
                    "updated_at": now,
                }
            },
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

    async def list_by_sandbox(
        self,
        sandbox_id: str,
    ) -> list[SessionWorkspaceDocument]:
        return await SessionWorkspaceDocument.find(
            {"sandbox_id": sandbox_id, "workspace_path": {"$ne": None}},
        ).to_list()