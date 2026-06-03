from dataclasses import dataclass
from typing import Optional

from chat.application.tools.web.services.web_search.enums import ProviderMode, SearcherName
from chat.application.tools.web.services.web_search.provider_policy.persistence import UserSearchProviderConfig
from chat.application.tools.web.services.web_search.provider_policy.service import (
    SearchProviderConfigService,
)


@dataclass(frozen=True, slots=True)
class SearchProviderConfigView:
    provider_mode: ProviderMode
    provider: Optional[SearcherName]
    masked_key: Optional[str]
    is_valid: bool


class SearchProviderConfigApiService:
    def __init__(self, *, service: SearchProviderConfigService) -> None:
        self._service = service

    async def get_config(self, *, user_id: str) -> SearchProviderConfigView:
        return _to_view(
            await self._service.get_config(
                    user_id=user_id
                )
            )

    async def set_mode(
        self,
        *,
        user_id: str,
        mode: ProviderMode,
    ) -> SearchProviderConfigView:
        return _to_view(await self._service.set_mode(user_id=user_id, mode=mode))

    async def set_custom_provider(
        self,
        *,
        user_id: str,
        provider: SearcherName,
        api_key: str,
    ) -> SearchProviderConfigView:
        return _to_view(
            await self._service.set_custom_provider(
                user_id=user_id,
                provider=provider,
                api_key=api_key,
            )
        )

    async def clear_custom_provider(self, *, user_id: str) -> SearchProviderConfigView:
        return _to_view(await self._service.clear_custom_provider(user_id=user_id))

    async def verify(self, *, user_id: str) -> SearchProviderConfigView:
        return _to_view(await self._service.verify(user_id=user_id))


def _to_view(
    config: Optional[UserSearchProviderConfig],
) -> SearchProviderConfigView:
    if config is None:
        return SearchProviderConfigView(
            provider_mode=ProviderMode.DEFAULT,
            provider=None,
            masked_key=None,
            is_valid=True,
        )

    return SearchProviderConfigView(
        provider_mode=config.provider_mode,
        provider=config.provider,
        masked_key=config.masked_key,
        is_valid=config.is_valid,
    )
