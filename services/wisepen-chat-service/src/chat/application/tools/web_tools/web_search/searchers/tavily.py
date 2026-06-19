from __future__ import annotations

import httpx

from .base import BaseProviderSearcher, SearchProviderConfig, SearchProviderCredentialError
from ..providers.models import SearchProviderName
from ..providers.tavily import TavilySearchRequest, map_tavily_response


class TavilySearcher(BaseProviderSearcher):
    provider = SearchProviderName.TAVILY
    request_class = TavilySearchRequest
    response_mapper = staticmethod(map_tavily_response)

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        config: SearchProviderConfig,
    ) -> None:
        if not config.api_key:
            raise SearchProviderCredentialError("Tavily API key is required.")
        headers = {
            "Authorization": f"Bearer {config.api_key}",
        }
        super().__init__(http_client=http_client, config=config, headers=headers)
