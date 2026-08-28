from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from common.core.exceptions import ServiceException

from sandbox.domain.entities import SandboxDocument, SandboxState
from sandbox.domain.error_codes import SandboxErrorCode

from beanie.operators import In
from beanie import UpdateResponse

from sandbox.domain.repositories import SandboxRepository


class MongoSandboxRepository(SandboxRepository):
    """SandboxDocument 的 MongoDB 仓储实现 """

    async def save(self, sandbox: SandboxDocument) -> None:
        await sandbox.save()

    async def get_by_id(self, sandbox_id: str) -> SandboxDocument | None:
        return await SandboxDocument.find_one(
            SandboxDocument.sandbox_id == sandbox_id,
        )

    async def get_by_states(
        self,
        states: Iterable[SandboxState],
    ) -> list[SandboxDocument]:
        return await SandboxDocument.find(
            In(SandboxDocument.state, list(states)),
        ).to_list()

    async def count_by_state(self) -> dict[SandboxState, int]:
        counts = {state: 0 for state in SandboxState}
        pipeline = [{"$group": {"_id": "$state", "count": {"$sum": 1}}}]
        items = await SandboxDocument.aggregate(pipeline).to_list()
        for item in items:
            try:
                state = SandboxState(item["_id"])
            except (KeyError, ValueError):
                continue
            counts[state] = int(item.get("count") or 0)
        return counts

    async def get_by_user_binding(
        self,
        user_id: str,
    ) -> SandboxDocument | None:
        return await SandboxDocument.find_one(
            SandboxDocument.bind_user_id == user_id,
            SandboxDocument.state == SandboxState.USER_ACTIVE,
        )

    async def assign_to_user(
        self,
        user_id: str,
        session_id: str | None = None,
    ) -> SandboxDocument:
        now = datetime.now(timezone.utc)
        updates: dict[str, object] = {
            "state": SandboxState.USER_ACTIVE,
            "bind_user_id": user_id,
            "bind_at": now,
            "updated_at": now,
            "idle_since": None,
        }
        updates["active_session_ids"] = [session_id] if session_id is not None else []
        sandbox = await SandboxDocument.find_one(
            {
                "state": SandboxState.READY,
                "bind_user_id": None,
            },
            sort=[("created_at", 1)],
        ).update(
            {
                "$set": updates
            },
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

        if sandbox is None:
            raise ServiceException(SandboxErrorCode.POOL_EMPTY,"sandbox pool has no READY container")
        return sandbox

    async def start_session(self, sandbox_id: str, session_id: str) -> SandboxDocument | None:
        now = datetime.now(timezone.utc)
        return await SandboxDocument.find_one(
            {"sandbox_id": sandbox_id, "state": SandboxState.USER_ACTIVE},
        ).update(
            {
                "$addToSet": {"active_session_ids": session_id},
                "$set": {"idle_since": None, "updated_at": now},
            },
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

    async def finish_session(self, sandbox_id: str, session_id: str) -> SandboxDocument | None:
        now = datetime.now(timezone.utc)
        sandbox = await SandboxDocument.find_one(
            {"sandbox_id": sandbox_id, "state": SandboxState.USER_ACTIVE},
        ).update(
            {"$pull": {"active_session_ids": session_id}, "$set": {"updated_at": now}},
            response_type=UpdateResponse.NEW_DOCUMENT,
        )
        if sandbox is None:
            return None
        if not sandbox.active_session_ids:
            sandbox = await SandboxDocument.find_one(
                {
                    "sandbox_id": sandbox_id,
                    "state": SandboxState.USER_ACTIVE,
                    "idle_since": None,
                    "$or": [{"active_session_ids": []}, {"active_session_ids": {"$exists": False}}],
                },
            ).update(
                {"$set": {"idle_since": now, "updated_at": now}},
                response_type=UpdateResponse.NEW_DOCUMENT,
            ) or sandbox
        return sandbox

    async def list_idle_user_sandboxes(self, cutoff: datetime) -> list[SandboxDocument]:
        return await SandboxDocument.find(
            {
                "state": SandboxState.USER_ACTIVE,
                "$or": [{"active_session_ids": []}, {"active_session_ids": {"$exists": False}}],
                "idle_since": {"$lte": cutoff},
            }
        ).to_list()

    async def claim_idle_sandbox(self, sandbox_id: str, idle_since: datetime) -> SandboxDocument | None:
        return await SandboxDocument.find_one(
            {
                "sandbox_id": sandbox_id,
                "state": SandboxState.USER_ACTIVE,
                "$or": [{"active_session_ids": []}, {"active_session_ids": {"$exists": False}}],
                "idle_since": idle_since,
            },
        ).update(
            {"$set": {"state": SandboxState.RETIRING, "updated_at": datetime.now(timezone.utc)}},
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

    async def change_state(
        self,
        sandbox_id: str,
        state: SandboxState,
        *,
        clear_user_binding: bool = False,
    ) -> SandboxDocument | None:
        filters: dict[str, object] = {"sandbox_id": sandbox_id}
        updates: dict[str, object] = {
            "state": state,
            "updated_at": datetime.now(timezone.utc),
        }
        if clear_user_binding:
            updates.update({"bind_user_id": None, "active_session_ids": [], "idle_since": None})
        return await SandboxDocument.find_one(
            filters,
        ).update(
            {
                "$set": updates,
            },
            response_type=UpdateResponse.NEW_DOCUMENT,
        )
