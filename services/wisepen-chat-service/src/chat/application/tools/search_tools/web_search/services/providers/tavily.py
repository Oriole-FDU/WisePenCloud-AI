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
    dedupe_by_url,
    has_search_result_fields,
)


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
        results = tuple(
            result
            for item in as_dict_tuple(data.get("results"))
            if (result := TavilySearcher._map_result(item)) is not None
        )

        return ProviderSearchResponse(
            query=query,
            provider=SearchProviderName.TAVILY,
            results=dedupe_by_url(
                results,
                url_getter=lambda item: item.url,
                limit=max_results,
            ),
            answer=as_str_or_none(data.get("answer")),
        )

    @staticmethod
    def _map_result(
        item: dict[str, object],
    ) -> ProviderSearchResult | None:
        title = as_str(item.get("title"))
        url = as_str(item.get("url"))

        if not has_search_result_fields(title=title, url=url):
            return None

        return ProviderSearchResult(
            title=title,
            url=url,
            preview=SearchPreview(
                overview=as_str_or_none(item.get("content")),
            ),
        )
