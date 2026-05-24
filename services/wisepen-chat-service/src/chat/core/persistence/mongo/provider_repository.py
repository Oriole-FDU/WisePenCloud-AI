from datetime import datetime, timezone
from typing import Any, List, Optional

from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from chat.domain.entities.model import ModelProviderMapping
from chat.domain.entities.provider import Provider, ProviderScope
from chat.domain.error_codes import ChatErrorCode
from chat.domain.repositories.provider_repo import ProviderRepository
from common.core.exceptions import ServiceException


class MongoProviderRepository(ProviderRepository):
    """用户 Provider 的 MongoDB 仓储实现。"""

    async def get_provider(
        self,
        provider_id: PydanticObjectId,
        user_id: Optional[str] = None,
    ) -> Provider:
        scope = self._scope_for(user_id)

        provider = await Provider.find_one(
            Provider.id == provider_id,
            Provider.scope == scope,
            Provider.owner_user_id == user_id,
        )
        if provider is None:
            raise ServiceException(ChatErrorCode.PROVIDER_NOT_FOUND)

        return provider

    async def list_providers(
        self,
        user_id: Optional[str] = None,
    ) -> List[Provider]:
        scope = self._scope_for(user_id)

        return await Provider.find(
            Provider.scope == scope,
            Provider.owner_user_id == user_id,
        ).sort("-is_active", "-updated_at").to_list()

    async def create_provider(
        self,
        provider: Provider,
        user_id: Optional[str] = None,
    ) -> Provider:
        now = datetime.now(timezone.utc)

        provider.scope = self._scope_for(user_id)
        provider.owner_user_id = user_id
        provider.is_active = True
        provider.api_key_fingerprint = self._mask_api_key(provider.api_key)
        provider.created_at = provider.created_at or now
        provider.updated_at = now

        try:
            await provider.insert()
        except DuplicateKeyError:
            raise ServiceException(ChatErrorCode.PROVIDER_ALREADY_EXISTS)

        return provider

    async def update_provider(
        self,
        provider_id: PydanticObjectId,
        updates: dict[str, Any],
        user_id: Optional[str] = None,
    ) -> Provider:
        provider = await self.get_provider(provider_id, user_id)

        if "name" in updates:
            provider.name = updates["name"]
        if "api_base_url" in updates:
            provider.api_base_url = updates["api_base_url"]
        if "api_key" in updates:
            provider.api_key = updates["api_key"]
            provider.api_key_fingerprint = self._mask_api_key(updates["api_key"])
        if "type" in updates:
            provider.type = updates["type"]
        if "is_active" in updates:
            provider.is_active = updates["is_active"]

        provider.updated_at = datetime.now(timezone.utc)

        try:
            await provider.save()
        except DuplicateKeyError:
            raise ServiceException(ChatErrorCode.PROVIDER_ALREADY_EXISTS)

        return provider

    async def remove_provider(
        self,
        provider_id: PydanticObjectId,
        user_id: Optional[str] = None,
    ) -> None:
        provider = await self.get_provider(provider_id, user_id)

        mapping_filters = [
            ModelProviderMapping.provider_id == provider_id,
            ModelProviderMapping.owner_user_id == user_id,
        ]

        mapping_count = await ModelProviderMapping.find(*mapping_filters).count()
        if mapping_count > 0:
            raise ServiceException(ChatErrorCode.PROVIDER_IN_USE)

        await provider.delete()

    async def increment_usage(
        self,
        provider_id: PydanticObjectId,
        user_id: Optional[str],
        usage_tokens: int,
        billable_usage_tokens: int = 0,
    ) -> None:
        if usage_tokens <= 0 and billable_usage_tokens <= 0:
            return

        result = await Provider.get_pymongo_collection().update_one(
            {
                "_id": provider_id,
                "scope": self._scope_for(user_id).value,
                "owner_user_id": user_id,
            },
            {
                "$inc": {
                    "usage_tokens": max(usage_tokens, 0),
                    "billable_usage_tokens": max(billable_usage_tokens, 0),
                },
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )
        if result.matched_count == 0:
            raise ServiceException(ChatErrorCode.PROVIDER_NOT_FOUND)

    @staticmethod
    def _scope_for(user_id: Optional[str]) -> ProviderScope:
        return ProviderScope.USER if user_id is not None else ProviderScope.SYSTEM

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        if len(api_key) <= 8:
            return "*" * len(api_key)

        return f"{api_key[:4]}***{api_key[-4:]}"
