from beanie.operators import Set

from chat.domain.entities.subagent import SubagentRun, SubagentRoot


class MongoSubagentRepository:
    async def save_root(self, root: SubagentRoot):
        await SubagentRoot.find_one({"root_execution_id": root.root_execution_id}).upsert(
            Set(root.model_dump(exclude={"id"})), on_insert=root,
        )

    async def get_root(self, user_id, session_id, root_execution_id):
        return await SubagentRoot.find_one({
            "user_id": user_id, "session_id": session_id, "root_execution_id": root_execution_id,
        })

    async def save_run(self, run: SubagentRun):
        await SubagentRun.find_one({"run_id": run.run_id}).upsert(
            Set(run.model_dump(exclude={"id"})), on_insert=run,
        )

    async def list_runs(self, user_id: str, session_id: str, root_execution_id: str | None = None):
        query = {"user_id": user_id, "session_id": session_id}
        if root_execution_id is not None:
            query["root_execution_id"] = root_execution_id
        return await SubagentRun.find(query).sort("-created_at").limit(256).to_list()

    async def get_run(self, user_id: str, session_id: str, child_id: str, run_id: str | None = None):
        query = {"user_id": user_id, "session_id": session_id, "child_id": child_id}
        if run_id is not None:
            query["run_id"] = run_id
        return await SubagentRun.find(query).sort("-created_at").first_or_none()
