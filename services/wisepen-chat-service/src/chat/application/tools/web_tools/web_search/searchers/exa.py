from __future__ import annotations

import httpx

from .base import BaseProviderSearcher, SearchProviderConfig, SearchProviderCredentialError
from ..providers.exa import ExaSearchRequest, map_exa_response
from ..providers.models import SearchProviderName


class ExaSearcher(BaseProviderSearcher):
    provider = SearchProviderName.EXA
    request_class = ExaSearchRequest
    response_mapper = staticmethod(map_exa_response)

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        config: SearchProviderConfig,
    ) -> None:
        if not config.api_key:
            raise SearchProviderCredentialError("Exa API key is required.")
        headers = {
            "x-api-key": config.api_key,
        }
        super().__init__(http_client=http_client, config=config, headers=headers)
