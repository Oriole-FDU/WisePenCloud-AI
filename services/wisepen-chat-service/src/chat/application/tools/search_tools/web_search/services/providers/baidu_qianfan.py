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
from .normalization import (
    as_dict_tuple,
    as_str,
    as_str_or_none,
    dedupe_by_url,
    has_search_result_fields,
)


@dataclass(frozen=True, slots=True)
class BaiduQianfanSearchRequest(ProviderSearchRequest):
    query: str
    max_results: int = 10

    def to_http_request(self) -> ProviderSearchHttpRequest:
        return ProviderSearchHttpRequest(
            method="POST",
            path="/v2/ai_search/web_search",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": self.query,
                    },
                ],
                "search_source": "baidu_search_v2",
                "resource_type_filter": [
                    {
                        "type": "web",
                        "top_k": self.max_results,
                    },
                ],
            },
        )


class BaiduQianfanSearcher(BaseProviderSearcher):
    provider = SearchProviderName.BAIDU_QIANFAN
    request_class = BaiduQianfanSearchRequest

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        config: SearchProviderConfig,
    ) -> None:
        if not config.api_key:
            raise SearchProviderCredentialError("Baidu Qianfan API key is required.")

        super().__init__(
            http_client=http_client,
            config=config,
            headers={
                "X-Appbuilder-Authorization": f"Bearer {config.api_key}",
            },
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
            for item in as_dict_tuple(data.get("references"))
            if (result := BaiduQianfanSearcher._map_result(item)) is not None
        )

        return ProviderSearchResponse(
            query=query,
            provider=SearchProviderName.BAIDU_QIANFAN,
            results=dedupe_by_url(
                results,
                url_getter=lambda item: item.url,
                limit=max_results,
            ),
            answer=(
                as_str_or_none(data.get("answer"))
                or as_str_or_none(data.get("content"))
            ),
        )

    @staticmethod
    def _map_result(
        item: dict[str, object],
    ) -> ProviderSearchResult | None:
        resource_type = as_str(item.get("type") or item.get("resource_type")).lower()

        if resource_type and resource_type != "web":
            return None

        title = as_str(item.get("title"))
        url = as_str(item.get("url"))

        if not has_search_result_fields(title=title, url=url):
            return None

        return ProviderSearchResult(
            title=title,
            url=url,
            preview=SearchPreview(
                overview=as_str_or_none(item.get("content") or item.get("snippet")),
            ),
        )
