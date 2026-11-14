from abc import ABC, abstractmethod
from typing import Any, List, Optional

from beanie import PydanticObjectId

from chat.domain.entities.provider import Provider, ProviderScope

class ProviderRepository(ABC):

    @abstractmethod
    async def get_provider(
        self,
        provider_id: PydanticObjectId,
        scope: ProviderScope,
        owner_user_id: Optional[str] = None,
    ) -> Provider: pass

    @abstractmethod
    async def list_providers(
        self,
        scope: ProviderScope,
        owner_user_id: Optional[str] = None,
    ) -> List[Provider]: pass

    @abstractmethod
    async def create_provider(
            self,
            provider: Provider,
            scope: ProviderScope,
            owner_user_id: Optional[str] = None
    ) -> Provider: pass

    @abstractmethod
    async def update_provider(
        self,
        provider_id: PydanticObjectId,
        updates: dict[str, Any],
        scope: ProviderScope,
        owner_user_id: Optional[str] = None,
    ) -> Provider: pass

    @abstractmethod
    async def remove_provider(
        self,
        provider_id: PydanticObjectId,
        scope: ProviderScope,
        owner_user_id: Optional[str] = None,
    ) -> None: pass

    @abstractmethod
    async def increment_usage(
        self,
        provider_id: PydanticObjectId,
        scope: ProviderScope,
        owner_user_id: Optional[str],
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        billable_tokens: int,
    ) -> None:
        pass
