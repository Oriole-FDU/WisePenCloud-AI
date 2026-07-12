from abc import ABC, abstractmethod
from typing import Any

from chat.domain.entities.tool_config import UserToolConfig


class ToolConfigRepository(ABC):
    @abstractmethod
    async def get_tool_config(self, user_id: str, tool_name: str) -> UserToolConfig | None:
        pass

    @abstractmethod
    async def list_tool_configs(self, user_id: str) -> list[UserToolConfig]:
        pass

    @abstractmethod
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
        pass

    @abstractmethod
    async def delete_tool_config(self, user_id: str, tool_name: str) -> None:
        pass
