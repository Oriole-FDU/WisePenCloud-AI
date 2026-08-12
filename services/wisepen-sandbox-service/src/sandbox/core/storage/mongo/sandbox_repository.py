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
        )

    async def assign_to_user(
            self,
            user_id: str,
    ) -> SandboxDocument:
        sandbox = SandboxDocument.find_one(
            {
                "state": SandboxState.READY,
                "bind_user_id": None,
            },
            sort=[("created_at", 1)],
        ).update(
            {
                "$set": {
                    "state": SandboxState.USER_ACTIVE,
                    "bind_user_id": user_id,
                }
            },
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

        if sandbox is None:
            raise ServiceException(SandboxErrorCode.POOL_EMPTY,"sandbox pool has no READY container")
        return sandbox

    async def change_state(
        self,
        sandbox_id: str,
        state: SandboxState,
    ) -> SandboxDocument | None:
        return SandboxDocument.find_one(
            SandboxDocument.sandbox_id == sandbox_id,
        ).update(
            {
                "$set": {
                    "state": state,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            response_type=UpdateResponse.NEW_DOCUMENT,
        )
