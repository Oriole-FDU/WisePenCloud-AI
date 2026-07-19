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
    has_search_result_fields,
)


@dataclass(frozen=True, slots=True)
class AnySearchRequest(ProviderSearchRequest):
    query: str
    max_results: int = 10

    def to_http_request(self) -> ProviderSearchHttpRequest:
        return ProviderSearchHttpRequest(
            method="POST",
            path="/v1/search",
            json={
                "query": self.query,
                "max_results": self.max_results,
                "content_types": ["webpage"],
            },
        )


class AnySearchSearcher(BaseProviderSearcher):
    provider = SearchProviderName.ANYSEARCH
    request_class = AnySearchRequest

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        config: SearchProviderConfig,
    ) -> None:
        if not config.api_key:
            raise SearchProviderCredentialError("AnySearch API key is required.")

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
        payload = data.get("data")
        if not isinstance(payload, dict):
            payload = data

        results = tuple(
            result
            for item in as_dict_tuple(payload.get("results"))
            if (result := AnySearchSearcher._map_result(item)) is not None
        )

        return ProviderSearchResponse(
            query=query,
            provider=SearchProviderName.ANYSEARCH,
            results=dedupe_by_url(
                results,
                url_getter=lambda item: item.url,
                limit=max_results,
            ),
            answer=as_str_or_none(payload.get("answer")),
        )

    @staticmethod
    def _map_result(
        item: dict[str, object],
    ) -> ProviderSearchResult | None:
        title = as_str(item.get("title"))
        url = as_str(item.get("url"))

        if not has_search_result_fields(title=title, url=url):
            return None

        overview = as_str_or_none(
            item.get("snippet") or item.get("description") or item.get("summary")
        )

        return ProviderSearchResult(
            title=title,
            url=url,
            preview=SearchPreview(
                overview=overview,
                highlights=as_str_tuple(item.get("highlights")),
            ),
        )
