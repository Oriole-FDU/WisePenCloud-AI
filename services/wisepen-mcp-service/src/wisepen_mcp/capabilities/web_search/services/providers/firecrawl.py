from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..models import (
    SearchProviderName,
    SearchResponse,
    SearchResult,
)
from ._utils import dedupe_results
from .base import (
    BaseProviderSearcher,
    ProviderSearchRequest,
    SearchProviderConfig,
    SearchProviderCredentialError,
)
from .models import ProviderSearchHttpRequest


@dataclass(frozen=True, slots=True)
class FirecrawlSearchRequest(ProviderSearchRequest):
    query: str
    max_results: int = 10
    academic: bool = False

    def to_http_request(self) -> ProviderSearchHttpRequest:
        payload: dict[str, object] = {
            "query": self.query,
            "limit": self.max_results,
            "sources": ["web"],
        }

        if self.academic:
            payload["categories"] = ["research"]

        return ProviderSearchHttpRequest(
            method="POST",
            path="/v2/search",
            json=payload,
        )


class FirecrawlSearcher(BaseProviderSearcher):
    provider = SearchProviderName.FIRECRAWL
    request_class = FirecrawlSearchRequest

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        config: SearchProviderConfig,
    ) -> None:
        if not config.api_key:
            raise SearchProviderCredentialError("Firecrawl API key is required.")

        super().__init__(
            http_client=http_client,
            config=config,
            headers={"Authorization": f"Bearer {config.api_key}"},
        )

    @staticmethod
    def map_response(
        data: dict[str, Any],
        *,
        query: str,
        max_results: int,
    ) -> SearchResponse:
        return SearchResponse(
            query=query,
            provider=SearchProviderName.FIRECRAWL,
            results=dedupe_results(
                (
                    SearchResult(
                        title=item.get("title"),
                        url=item.get("url"),
                        snippet=item.get("description"),
                    )
                    for item in data["data"]["web"]
                ),
                limit=max_results,
            ),
        )

    async def search_academic(
        self,
        *,
        query: str,
        max_results: int,
    ) -> SearchResponse:
        return await self._execute_request(
            request=FirecrawlSearchRequest(
                query=query,
                max_results=max_results,
                academic=True,
            ),
            query=query,
            max_results=max_results,
        )
