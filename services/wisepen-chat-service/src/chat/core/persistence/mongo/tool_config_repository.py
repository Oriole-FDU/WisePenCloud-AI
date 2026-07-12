from datetime import datetime, timezone
from typing import Any

from chat.domain.entities.tool_config import UserToolConfig
from chat.domain.repositories.tool_config_repo import ToolConfigRepository


class MongoToolConfigRepository(ToolConfigRepository):
    async def get_tool_config(self, user_id: str, tool_name: str) -> UserToolConfig | None:
        return await UserToolConfig.find_one(
            UserToolConfig.user_id == user_id,
            UserToolConfig.tool_name == tool_name,
        )

    async def list_tool_configs(self, user_id: str) -> list[UserToolConfig]:
        return await UserToolConfig.find(
            UserToolConfig.user_id == user_id,
        ).sort("-updated_at").to_list()

    async def upsert_tool_config(
        self,
        *,
        user_id: str,
        tool_name: str,
        enabled: bool,
        config: dict[str, Any],
        secret_config: dict[str, str],
        secret_fingerprints: dict[str, str],
        schema_version: int,
    ) -> UserToolConfig:
        now = datetime.now(timezone.utc)
        await UserToolConfig.get_pymongo_collection().update_one(
            {"user_id": user_id, "tool_name": tool_name},
            {
                "$set": {
                    "enabled": enabled,
                    "config": dict(config),
                    "secret_config": dict(secret_config),
                    "secret_fingerprints": dict(secret_fingerprints),
                    "schema_version": schema_version,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "user_id": user_id,
                    "tool_name": tool_name,
                    "created_at": now,
                },
            },
            upsert=True,
        )
        entity = await self.get_tool_config(user_id, tool_name)
        if entity is None:
            raise RuntimeError("failed to upsert tool config")
        return entity

    async def delete_tool_config(self, user_id: str, tool_name: str) -> None:
        existing = await self.get_tool_config(user_id, tool_name)
        if existing is not None:
            await existing.delete()
