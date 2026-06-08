from __future__ import annotations

import asyncio

import httpx

from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.errors import SearchProviderTransientError
from chat.application.tools.web.services.web_search.models import SearchResponse
from chat.application.tools.web.services.web_search.schemas.perplexity import (
    PerplexitySearchRequest,
    map_perplexity_response,
)
from chat.application.tools.web.services.web_search.searcher.base import WebSearcher
from chat.application.tools.web.services.web_search.utils.http_client import fetch_search_json
from common.logger import log_event

_PERPLEXITY_SEARCH_ENDPOINT = "/search"


class PerplexitySearcher(WebSearcher):
    def __init__(
            self,
            client: httpx.AsyncClient,
            *,
            api_key: str,
            base_url: str,
            concurrency: int = 5,
    ) -> None:
        self._request_url = f"{base_url.rstrip('/')}{_PERPLEXITY_SEARCH_ENDPOINT}"
        self._api_key = api_key.strip()
        self._semaphore = asyncio.Semaphore(concurrency)

        self._headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    @property
    def name(self) -> SearcherName:
        return SearcherName.PERPLEXITY

    async def search(
            self,
            query: str,
            *,
            max_results: int = 10,
            timeout_seconds: float = 8.0,
    ) -> SearchResponse:

        request = PerplexitySearchRequest(query=query, max_results=max_results)

        data = await fetch_search_json(
            client=self._client,
            url=self._request_url,
            payload=request.to_payload(),
            query=query,
            timeout_seconds=timeout_seconds,
            semaphore=self._semaphore,
            provider_name=self.name.value,
            headers=self._headers,
        )

        if not isinstance(data, dict):
            log_event(
                "Perplexity 返回了畸形非字典结构",
                data_type=type(data).__name__,
                query=query
            )

            return SearchResponse(
                query=query,
                results=[],
                source=self.name,
            )

        response = map_perplexity_response(
            data,
            query=query,
            max_results=max_results,
        )

        # 空结果抛出可重试异常（上游无结果，非调用错误）
        if not response.results:
            raise SearchProviderTransientError(
                provider=self.name.value,
                queries=[query],
                reason="Perplexity empty_result returned from upstream source",
            )

        return response