from __future__ import annotations

import httpx

from .base import BaseProviderSearcher, SearchProviderConfig, SearchProviderCredentialError
from ..providers.models import SearchProviderName
from ..providers.serper import SerperSearchRequest, map_serper_response


class SerperSearcher(BaseProviderSearcher):
    """Serper.dev 搜索器（Google SERP API）。"""

    provider = SearchProviderName.SERPER
    request_class = SerperSearchRequest
    response_mapper = staticmethod(map_serper_response)

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        config: SearchProviderConfig,
    ) -> None:
        if not config.api_key:
            raise SearchProviderCredentialError("Serper API key is required.")
        headers = {
            "X-API-KEY": config.api_key,
        }
        super().__init__(http_client=http_client, config=config, headers=headers)
