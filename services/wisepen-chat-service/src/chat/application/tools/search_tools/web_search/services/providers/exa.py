from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..models import (
    SearchProviderName,
    SearchResponse,
    SearchResult,
)
from .base import (
    BaseProviderSearcher,
    ProviderSearchRequest,
    SearchProviderConfig,
    SearchProviderCredentialError,
)
from .models import ProviderSearchHttpRequest
from ._utils import dedupe_results


@dataclass(frozen=True, slots=True)
class ExaSearchRequest(ProviderSearchRequest):
    query: str
    max_results: int = 10
    academic: bool = False

    def to_http_request(self) -> ProviderSearchHttpRequest:
        payload: dict[str, object] = {
            "query": self.query,
            "type": "auto",
            "numResults": self.max_results,
            "contents": {
                "highlights": True,
                "summary": True,
                "text": False,
            },
        }

        if self.academic:
            payload["category"] = "research paper"

        return ProviderSearchHttpRequest(
            method="POST",
            path="/search",
            json=payload,
        )


class ExaSearcher(BaseProviderSearcher):
    provider = SearchProviderName.EXA
    request_class = ExaSearchRequest

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        config: SearchProviderConfig,
    ) -> None:
        if not config.api_key:
            raise SearchProviderCredentialError("Exa API key is required.")

        super().__init__(
            http_client=http_client,
            config=config,
            headers={"x-api-key": config.api_key},
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
            provider=SearchProviderName.EXA,
            results=dedupe_results(
                (
                    SearchResult(
                        title=item.get("title"),
                        url=item.get("url"),
                        snippet=item.get("summary"),
                        highlights=(
                            tuple(highlights)
                            if (highlights := item.get("highlights")) is not None
                            else None
                        ),
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
            request=ExaSearchRequest(
                query=query,
                max_results=max_results,
                academic=True,
            ),
            query=query,
            max_results=max_results,
        )
