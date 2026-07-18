from abc import ABC, abstractmethod
from typing import Any

from chat.domain.entities.mcp_tool_server_config import McpToolStatus, UserMcpServerConfig


class McpServerConfigRepository(ABC):
    @abstractmethod
    async def get_server_config(self, user_id: str, server_id: str) -> UserMcpServerConfig | None:
        pass

    @abstractmethod
    async def list_server_configs(self, user_id: str) -> list[UserMcpServerConfig]:
        pass

    @abstractmethod
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
        enabled_tool_names: list[str]
    ) -> UserMcpServerConfig:
        pass

    @abstractmethod
    async def delete_server_config(self, user_id: str, server_id: str) -> None:
        pass
