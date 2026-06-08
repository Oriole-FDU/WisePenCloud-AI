from __future__ import annotations

import asyncio
from typing import Optional

import httpx

from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.errors import SearchProviderTransientError
from chat.application.tools.web.services.web_search.models import SearchResponse
from chat.application.tools.web.services.web_search.schemas.anysearch import (
    AnySearchRequest,
    map_anysearch_response,
)
from chat.application.tools.web.services.web_search.searcher.base import WebSearcher
from chat.application.tools.web.services.web_search.utils.http_client import fetch_search_json
from common.logger import log_event

_ANYSEARCH_ENDPOINT = "/v1/search"


class AnySearchSearcher(WebSearcher):
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: Optional[str] = None,
        base_url: str = "https://api.anysearch.com",
        concurrency: int = 5,
    ) -> None:
        self._client = client
        self._request_url = f"{base_url.rstrip('/')}{_ANYSEARCH_ENDPOINT}"
        self._semaphore = asyncio.Semaphore(concurrency)
        self._headers = {"Content-Type": "application/json"}
        if api_key and api_key.strip():
            self._headers["Authorization"] = f"Bearer {api_key.strip()}"

    @property
    def name(self) -> SearcherName:
        return SearcherName.ANYSEARCH

    async def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        timeout_seconds: float = 8.0,
    ) -> SearchResponse:
        request = AnySearchRequest(query=query, max_results=max_results)

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
                "AnySearch 返回了畸形非字典结构",
                data_type=type(data).__name__,
                query=query,
            )
            return SearchResponse(query=query, results=[], source=self.name)

        response = map_anysearch_response(
            data,
            query=query,
            max_results=max_results,
        )
        if not response.results:
            raise SearchProviderTransientError(
                provider=self.name.value,
                queries=[query],
                reason="AnySearch empty_result returned from upstream source",
            )

        log_event(
            "AnySearch custom provider 调用完成",
            result_count=len(response.results),
            source=self.name.value,
        )
        return response