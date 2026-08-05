import json
from datetime import datetime, timezone

from chat.core.persistence.redis.base import RedisRepository
from chat.domain.entities.mcp_tool_server_config import McpToolDescriptor
from chat.domain.repositories.mcp_tool_discovery_cache_repo import McpToolDiscoveryCacheRepository
from common.logger import warn
from redis.asyncio import Redis


class RedisMcpToolDiscoveryCache(RedisRepository, McpToolDiscoveryCacheRepository):
    def __init__(self, *, redis_client: Redis) -> None:
        super().__init__(redis_client=redis_client)

    def _get_user_key(self, *, user_id: str, server_id: str, config_updated_at: datetime) -> str:
        if config_updated_at.tzinfo is None:
            config_updated_at = config_updated_at.replace(tzinfo=timezone.utc)
        version = config_updated_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
        return f"wisepen:chat:mcp_tools:{user_id}:{server_id}:{version}"

    def _serialize(self, tools: list[McpToolDescriptor]) -> str:
        return json.dumps([tool.model_dump(mode="json") for tool in tools], ensure_ascii=False)

    def _deserialize(self, value: str) -> list[McpToolDescriptor]:
        payload = json.loads(value)
        return [McpToolDescriptor.model_validate(item) for item in payload]

    async def get_user_tools(
            self,
            *,
            user_id: str,
            server_id: str,
            config_updated_at: datetime,
    ) -> list[McpToolDescriptor] | None:
        key = self._get_user_key(
            user_id=user_id,
            server_id=server_id,
            config_updated_at=config_updated_at,
        )
        try:
            value = await self._redis.get(key)
            if value is None:
                return None
            return self._deserialize(str(value))
        except Exception as e:
            warn("get user MCP tool discovery cache failed.", key=key, exc=e)
            return None

    async def set_user_tools(
            self,
            *,
            user_id: str,
            server_id: str,
            config_updated_at: datetime,
            tools: list[McpToolDescriptor],
            ttl_seconds: int,
    ) -> None:
        key = self._get_user_key(
            user_id=user_id,
            server_id=server_id,
            config_updated_at=config_updated_at,
        )
        try:
            ttl = max(1, int(ttl_seconds))
            await self._redis.set(key, self._serialize(tools), ex=ttl)
        except Exception as e:
            warn("set user MCP tool discovery cache failed.", key=key, exc=e)
