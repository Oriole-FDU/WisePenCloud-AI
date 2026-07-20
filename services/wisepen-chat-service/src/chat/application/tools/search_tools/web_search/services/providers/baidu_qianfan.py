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
    ) -> SearchResponse:
        return SearchResponse(
            query=query,
            provider=SearchProviderName.BAIDU_QIANFAN,
            results=dedupe_results(
                (
                    SearchResult(
                        title=item.get("title"),
                        url=item.get("url"),
                        snippet=item.get("snippet"),
                    )
                    for item in data["references"]
                ),
                limit=max_results,
            ),
        )
