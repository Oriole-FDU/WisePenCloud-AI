from __future__ import annotations

from sandbox.domain.entities import CommandTaskDocument, CommandTaskStatus
from sandbox.domain.repositories import CommandTaskRepository


class MongoCommandTaskRepository(CommandTaskRepository):
    """命令任务的 MongoDB 存储实现。"""

    async def save(self, task: CommandTaskDocument) -> None:
        await task.save()

    async def get_by_id(self, task_id: str) -> CommandTaskDocument | None:
        return await CommandTaskDocument.find_one(CommandTaskDocument.task_id == task_id)

    async def update(self, task: CommandTaskDocument) -> None:
        await task.save()

    async def list_unfinished(self) -> list[CommandTaskDocument]:
        return await CommandTaskDocument.find(
            {"status": {"$in": [CommandTaskStatus.PENDING, CommandTaskStatus.RUNNING]}},
        ).to_list()

    async def list_by_sandbox(self, sandbox_id: str) -> list[CommandTaskDocument]:
        return await CommandTaskDocument.find({"sandbox_id": sandbox_id}).to_list()
