from abc import ABC, abstractmethod
from typing import Any, List, Optional

from beanie import PydanticObjectId

from chat.domain.entities.provider import Provider

class ProviderRepository(ABC):

    @abstractmethod
    async def get_provider(self, provider_id: PydanticObjectId, user_id: Optional[str] = None) -> Provider: pass

    @abstractmethod
    async def list_providers(self, user_id: Optional[str] = None) -> List[Provider]: pass

    @abstractmethod
    async def create_provider(self, provider: Provider, user_id: Optional[str] = None) -> Provider: pass

    @abstractmethod
    async def update_provider(
        self,
        provider_id: PydanticObjectId,
        updates: dict[str, Any],
        user_id: Optional[str] = None,
    ) -> Provider: pass

    @abstractmethod
    async def remove_provider(self, provider_id: PydanticObjectId, user_id: Optional[str] = None) -> None: pass

    @abstractmethod
    async def increment_usage(
        self,
        provider_id: PydanticObjectId,
        user_id: Optional[str],
        usage_tokens: int,
        billable_usage_tokens: int = 0,
    ) -> None:
        pass
