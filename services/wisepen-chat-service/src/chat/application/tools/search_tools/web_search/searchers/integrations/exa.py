from __future__ import annotations

import httpx

from chat.application.tools.search_tools.web_search.providers.exa import ExaSearchRequest, map_exa_response
from chat.application.tools.search_tools.web_search.providers.models import SearchProviderName
from chat.application.tools.search_tools.web_search.searchers.base import (
    BaseProviderSearcher,
    SearchProviderConfig,
    SearchProviderCredentialError,
)


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

    async def search_academic(
            self,
            *,
            query: str,
            max_results: int,
    ):
        request = ExaSearchRequest(
            query=query,
            max_results=max_results,
            academic=True,
        )
        return await self._execute_request(
            request=request,
            query=query,
            max_results=max_results,
        )
