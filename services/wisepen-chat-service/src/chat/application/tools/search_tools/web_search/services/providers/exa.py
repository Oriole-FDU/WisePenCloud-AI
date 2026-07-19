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
    SearchPreview,
    SearchProviderName,
)
from ._utils import (
    as_dict_tuple,
    as_str,
    as_str_or_none,
    as_str_tuple,
    dedupe_by_url,
    is_valid_search_result,
)


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
    ) -> ProviderSearchResponse:
        results = tuple(
            result
            for item in as_dict_tuple(data.get("results"))
            if (result := ExaSearcher._map_result(item)) is not None
        )

        return ProviderSearchResponse(
            query=query,
            provider=SearchProviderName.EXA,
            results=dedupe_by_url(
                results,
                url_getter=lambda item: item.url,
                limit=max_results,
            ),
        )

    @staticmethod
    def _map_result(
        item: dict[str, object],
    ) -> ProviderSearchResult | None:
        title = as_str(item.get("title"))
        url = as_str(item.get("url"))

        if not is_valid_search_result(title=title, url=url):
            return None

        return ProviderSearchResult(
            title=title,
            url=url,
            preview=SearchPreview(
                snippet=as_str_or_none(item.get("summary")),
                highlights=as_str_tuple(item.get("highlights")),
            ),
        )

    async def search_academic(
        self,
        *,
        query: str,
        max_results: int,
    ) -> ProviderSearchResponse:
        return await self._execute_request(
            request=ExaSearchRequest(
                query=query,
                max_results=max_results,
                academic=True,
            ),
            query=query,
            max_results=max_results,
        )
