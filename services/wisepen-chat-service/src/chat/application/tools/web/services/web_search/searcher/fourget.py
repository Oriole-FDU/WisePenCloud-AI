from __future__ import annotations

import asyncio
from typing import Optional

import httpx

from chat.application.tools.web.services.web_search.enums import SearcherName
from chat.application.tools.web.services.web_search.errors import (
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)
from chat.application.tools.web.services.web_search.models import SearchResponse
from chat.application.tools.web.services.web_search.schemas.fourget import (
    FourGetSearchRequest,
    map_fourget_html,
)
from common.logger import log_event
from .base import WebSearcher

FOURGET_ENDPOINT = "/web"


class FourGetSearcher(WebSearcher):
    def __init__(
            self,
            client: httpx.AsyncClient,
            *,
            base_url: str,
            user_agent: str = "WisePenCloud-AI web_search/1.0",
            timeout: float = 8.0,
            scraper: Optional[str] = None,
            max_concurrency: int = 5,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        self._request_url = f"{normalized_base_url}{FOURGET_ENDPOINT}"
        self._client = client
        self._timeout = timeout
        self._scraper = scraper
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml",
        }

    @property
    def name(self) -> SearcherName:
        return SearcherName.FOURGET

    async def search(
            self,
            query: str,
            *,
            max_results: int = 10,
    ) -> SearchResponse:
        request = FourGetSearchRequest(query=query, scraper=self._scraper)

        timeout = httpx.Timeout(
            timeout=self._timeout,
            connect=min(3.0, self._timeout),
            read=self._timeout,
        )

        try:
            async with self._semaphore:
                response = await self._client.get(
                    url=self._request_url,
                    params=request.to_params(),
                    headers=self._headers,
                    timeout=timeout,
                )

            if response.status_code == 429:
                try:
                    retry_after = int(response.headers.get("retry-after", "0"))
                except ValueError:
                    retry_after = 0

                raise SearchRateLimitError(
                    provider=self.name.value,
                    retry_after=retry_after,
                )

            response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "html" not in content_type.lower():
                raise SearchProviderError(
                    provider=self.name.value,
                    status_code=response.status_code,
                    reason=f"unexpected_content_type:{content_type}",
                )

            search_response = map_fourget_html(
                response.text,
                query=query,
                scraper=self._scraper,
                max_results=max_results,
            )

            log_event(
                "FourGet response mapped",
                query=query,
                request_url=self._request_url,
                final_url=str(response.url),
                status_code=response.status_code,
                content_type=content_type,
                html_length=len(response.text),
                normalized_result_count=len(search_response.results),
            )

            return search_response

        except httpx.TimeoutException as e:
            raise SearchTimeoutError(
                provider=self.name.value,
                queries=[query],
                timeout=self._timeout,
            ) from e

        except httpx.HTTPStatusError as e:
            raise SearchProviderError(
                provider=self.name.value,
                status_code=e.response.status_code,
                reason="http_status_error",
            ) from e

        except httpx.RequestError as e:
            raise SearchProviderError(
                provider=self.name.value,
                status_code=0,
                reason=f"connection_failed:{str(e)}",
            ) from e