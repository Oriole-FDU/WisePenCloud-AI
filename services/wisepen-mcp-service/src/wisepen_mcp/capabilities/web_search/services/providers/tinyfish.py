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
class TinyFishSearchRequest(ProviderSearchRequest):
    query: str
    max_results: int = 10
    academic: bool = False

    def to_http_request(self) -> ProviderSearchHttpRequest:
        params: dict[str, object] = {"query": self.query}

        if self.academic:
            params["domain_type"] = "research_paper"

        return ProviderSearchHttpRequest(
            method="GET",
            path="",
            params=params,
        )


class TinyFishSearcher(BaseProviderSearcher):
    provider = SearchProviderName.TINYFISH
    request_class = TinyFishSearchRequest

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        config: SearchProviderConfig,
    ) -> None:
        if not config.api_key:
            raise SearchProviderCredentialError("TinyFish API key is required.")

        super().__init__(
            http_client=http_client,
            config=config,
            headers={"X-API-Key": config.api_key},
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
            provider=SearchProviderName.TINYFISH,
            results=dedupe_results(
                (
                    SearchResult(
                        title=item.get("title"),
                        url=item.get("url"),
                        snippet=item.get("snippet"),
                    )
                    for item in data["results"]
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
            request=TinyFishSearchRequest(
                query=query,
                max_results=max_results,
                academic=True,
            ),
            query=query,
            max_results=max_results,
        )
