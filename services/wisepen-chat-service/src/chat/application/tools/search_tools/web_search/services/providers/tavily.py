from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .base import BaseProviderSearcher, SearchProviderConfig
from .core.errors import SearchProviderCredentialError
from .core.models import (
    ProviderSearchHttpRequest,
    ProviderSearchRequest,
    ProviderSearchResponse,
    ProviderSearchResult,
    SearchProviderName,
)
from ._utils import dedupe_results


@dataclass(frozen=True, slots=True)
class TavilySearchRequest(ProviderSearchRequest):
    query: str
    max_results: int = 10

    def to_http_request(self) -> ProviderSearchHttpRequest:
        return ProviderSearchHttpRequest(
            method="POST",
            path="/search",
            json={
                "query": self.query,
                "max_results": self.max_results,
                "include_answer": True,
                "include_raw_content": False,
                "include_images": False,
            },
        )


class TavilySearcher(BaseProviderSearcher):
    provider = SearchProviderName.TAVILY
    request_class = TavilySearchRequest

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        config: SearchProviderConfig,
    ) -> None:
        if not config.api_key:
            raise SearchProviderCredentialError("Tavily API key is required.")

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
    ) -> ProviderSearchResponse:
        return ProviderSearchResponse(
            query=query,
            provider=SearchProviderName.TAVILY,
            results=dedupe_results(
                (
                    ProviderSearchResult(
                        title=item.get("title"),
                        url=item.get("url"),
                        snippet=item.get("content"),
                    )
                    for item in data["results"]
                ),
                limit=max_results,
            ),
            answer=data["answer"],
        )
