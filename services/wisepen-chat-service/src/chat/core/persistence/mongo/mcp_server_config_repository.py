from datetime import datetime, timezone
from typing import Any

from chat.domain.entities.mcp_tool_server_config import McpToolStatus, UserMcpServerConfig
from chat.domain.error_codes import ChatErrorCode
from chat.domain.repositories.mcp_server_config_repo import McpServerConfigRepository
from common.core.exceptions import ServiceException


class MongoMcpServerConfigRepository(McpServerConfigRepository):
    async def get_server_config(self, user_id: str, server_id: str) -> UserMcpServerConfig | None:
        return await UserMcpServerConfig.find_one(
            UserMcpServerConfig.user_id == user_id,
            UserMcpServerConfig.server_id == server_id,
        )

    async def list_server_configs(self, user_id: str) -> list[UserMcpServerConfig]:
        return await UserMcpServerConfig.find(
            UserMcpServerConfig.user_id == user_id,
        ).sort("-updated_at").to_list()

    async def upsert_server_config(
        self,
        *,
        user_id: str,
        server_id: str,
        display_name: str,
        url: str,
        enabled: bool,
        headers: dict[str, str],
        secret_headers: dict[str, str],
        secret_header_fingerprints: dict[str, str],
        enabled_tool_names: list[str],
    ) -> UserMcpServerConfig:
        now = datetime.now(timezone.utc)
        update_fields: dict[str, Any] = {
            "display_name": display_name,
            "url": url,
            "enabled": enabled,
            "headers": dict(headers),
            "secret_headers": dict(secret_headers),
            "secret_header_fingerprints": dict(secret_header_fingerprints),
            "enabled_tool_names": list(enabled_tool_names),
            "updated_at": now,
        }
        await UserMcpServerConfig.get_pymongo_collection().update_one(
            {"user_id": user_id, "server_id": server_id},
            {
                "$set": update_fields,
                "$setOnInsert": {
                    "user_id": user_id,
                    "server_id": server_id,
                    "created_at": now,
                },
            },
            upsert=True,
        )
        entity = await self.get_server_config(user_id, server_id)
        if entity is None:
            raise ServiceException(ChatErrorCode.MCP_TOOL_CONFIG_NOT_FOUND)
        return entity

    async def delete_server_config(self, user_id: str, server_id: str) -> None:
        existing = await self.get_server_config(user_id, server_id)
        if existing is not None:
            await existing.delete()
