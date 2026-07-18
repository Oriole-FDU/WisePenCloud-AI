from abc import ABC, abstractmethod
from datetime import datetime
from chat.domain.entities.mcp_tool_server_config import McpToolDescriptor


class McpToolDiscoveryCacheRepository(ABC):
    @abstractmethod
    async def get_user_tools(
        self,
        *,
        user_id: str,
        server_id: str,
        config_updated_at: datetime,
    ) -> list[McpToolDescriptor] | None:
        pass

    @abstractmethod
    async def set_user_tools(
        self,
        *,
        user_id: str,
        server_id: str,
        config_updated_at: datetime,
        tools: list[McpToolDescriptor],
        ttl_seconds: int,
    ) -> None:
        pass